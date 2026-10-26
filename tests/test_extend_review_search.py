import copy
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
import shapely
from pyproj import Transformer
from shapely.geometry import box, mapping, shape

from puertogt.review_queue import digest, freeze_queue

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def builder():
    spec = importlib.util.spec_from_file_location(
        "extend_review_search", ROOT / "scripts/extend_review_search.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project(geometry, source, target):
    return shapely.transform(
        geometry, Transformer.from_crs(source, target, always_xy=True).transform,
        interleaved=False,
    )


def write_parent(path, manifest):
    manifest = copy.deepcopy(manifest)
    manifest.pop("queue_id", None)
    manifest = {"queue_id": "train-" + digest(manifest)[:20], **manifest}
    path.write_text(json.dumps({"manifest": manifest, "sha256": digest(manifest)}))
    return json.loads(path.read_text())["manifest"]


@pytest.fixture
def sources(builder, tmp_path, monkeypatch):
    zone = box(738000, 1536000, 738500, 1536500)
    land = box(738650, 1536100, 739100, 1536400)
    water_margin = zone.buffer(500).difference(zone).difference(land)
    entries = []
    zones = ["prq_fondeo", "prq_fondeo", "prq_muelles", "prq_fondeo",
             "pbr_canal", "pbr_canal", "pbr_canal", "pbr_canal",
             "stc_fondeo", "stc_fondeo", "stc_fondeo", "stc_muelles"]
    for i, name in enumerate(zones):
        bounds = ([737800, 1535800, 738800, 1536800] if i == 0
                  else [737000, 1535000, 739500, 1537500])
        entries.append({
            "observation_id": f"synthetic-{i}", "port_id": name[:3], "zone_id": name,
            "captured_at": "2025-02-07T00:14:16Z", "sensor": "sentinel-1",
            "zone_geometry": mapping(project(zone, 32615, 4326)),
            "reason": "Historical reason, preserved verbatim", "observable_fraction": 1.0,
            "assets": [{"path": "must-not-read/models-or-labels.png", "sha256": "a" * 64,
                        "mime_type": "image/png", "crs": "EPSG:32615", "bounds": bounds}],
        })
    parent_path = tmp_path / "parent.json"
    parent = write_parent(parent_path, {
        "schema_version": "blind-train-queue-v1", "split": "train", "entries": entries,
        "source_sha256": {"ports": "b" * 64, "selector": "c" * 64},
        "policy": {"sampling_purpose": "Historical TRAIN selection"},
        "distribution": {"preserve": [1, 2]}, "exclusions": [{"preserve": True}],
    })
    parent_hash = hashlib.sha256(parent_path.read_bytes()).hexdigest()
    proposal = {
        "schema": "anchorage-search-margins-proposal-v1", "active": False,
        "active_configuration_changed": False,
        "sources": [{"path": builder.PARENT_QUEUE_RELATIVE, "sha256": parent_hash},
                    {"path": "config/ports.geojson", "sha256": "b" * 64}],
        "geometries": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"port_id": "prq", "zone_id": "prq_fondeo",
                                               "radius_m": 500},
             "geometry": mapping(project(water_margin, 32615, 4326))},
        ]},
    }
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(json.dumps(proposal))
    monkeypatch.setattr(builder, "PARENT_QUEUE_SHA256", parent_hash)
    monkeypatch.setattr(builder, "PROPOSAL_SHA256",
                        hashlib.sha256(proposal_path.read_bytes()).hexdigest())
    return parent_path, proposal_path, parent, zone, land


def test_all_entries_assets_order_and_reference_are_preserved(builder, sources):
    parent_path, proposal_path, parent, _, _ = sources
    original_bytes = parent_path.read_bytes()
    derived = builder.build_search_queue(parent_path, proposal_path)
    assert parent_path.read_bytes() == original_bytes
    assert derived["schema_version"] == "blind-train-queue-v2"
    assert derived["queue_id"] != parent["queue_id"]
    payload = {k: v for k, v in derived.items() if k != "queue_id"}
    assert derived["queue_id"] == "train-" + digest(payload)[:20]
    assert len(derived["entries"]) == 12
    for old, new in zip(parent["entries"], derived["entries"], strict=True):
        assert {k: v for k, v in new.items() if k != "search_scope"} == old
        assert ("search_scope" in new) == (old["zone_id"] == "prq_fondeo")
    assert derived["distribution"] == parent["distribution"]
    assert derived["exclusions"] == parent["exclusions"]
    policy = derived["policy"]["search_scope_derivation"]
    assert policy["previously_reviewed_train"] is True
    assert policy["is_new_blind_sample"] is False
    assert policy["is_validation"] is False
    assert policy["margin_is_official_zone"] is False
    assert derived == builder.build_search_queue(parent_path, proposal_path)


def test_water_geometry_and_native_frame_clip_are_respected(builder, sources):
    parent_path, proposal_path, _, zone, land = sources
    derived = builder.build_search_queue(parent_path, proposal_path)
    scopes = []
    for entry in derived["entries"]:
        if "search_scope" not in entry:
            continue
        scope = entry["search_scope"]
        assert set(scope) == {"schema_version", "radius_m", "geometry", "geometry_sha256",
                              "reference_zone_sha256", "source_sha256", "frame_clipped",
                              "observability"}
        assert scope["schema_version"] == "water-search-scope-v1"
        assert scope["radius_m"] == 500 and scope["observability"] == "not_measured"
        assert scope["geometry_sha256"] == digest(scope["geometry"])
        assert scope["reference_zone_sha256"] == digest(entry["zone_geometry"])
        assert scope["source_sha256"] == builder.PROPOSAL_SHA256
        geometry = project(shape(scope["geometry"]), 4326, 32615)
        assert geometry.buffer(.001).covers(zone)
        assert geometry.intersection(land).area < .01
        assert geometry.difference(box(*entry["assets"][0]["bounds"]).buffer(.001)).area < .01
        assert geometry.area > zone.area
        scopes.append(scope)
    assert [s["frame_clipped"] for s in scopes] == [True, False, False]


def test_clipping_uses_the_native_frame_crs(builder, sources, monkeypatch):
    parent_path, proposal_path, parent, _, _ = sources
    asset = parent["entries"][0]["assets"][0]
    native_frame = project(box(*asset["bounds"]), 32615, 3857).envelope
    asset.update(crs="EPSG:3857", bounds=list(native_frame.bounds))
    write_parent(parent_path, parent)
    parent_hash = hashlib.sha256(parent_path.read_bytes()).hexdigest()
    monkeypatch.setattr(builder, "PARENT_QUEUE_SHA256", parent_hash)
    proposal = json.loads(proposal_path.read_text())
    proposal["sources"][0]["sha256"] = parent_hash
    proposal_path.write_text(json.dumps(proposal))
    monkeypatch.setattr(builder, "PROPOSAL_SHA256",
                        hashlib.sha256(proposal_path.read_bytes()).hexdigest())
    entry = builder.build_search_queue(parent_path, proposal_path)["entries"][0]
    geometry = project(shape(entry["search_scope"]["geometry"]), 4326, 3857)
    assert entry["search_scope"]["frame_clipped"] is True
    assert geometry.difference(native_frame.buffer(.001)).area < .01
    assert geometry.area > 0


def test_no_asset_model_label_or_catalogue_lookup(builder, sources, monkeypatch):
    parent_path, proposal_path, _, _, _ = sources
    allowed = {parent_path.resolve(), proposal_path.resolve(), Path(builder.__file__).resolve()}
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        assert path.resolve() in allowed, f"Unexpected metadata-external read: {path}"
        return original_open(path, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("Catalogue access is forbidden in this builder")

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    result = builder.build_search_queue(parent_path, proposal_path)
    assert len(result["entries"]) == 12


def test_expected_source_hashes_are_retained_and_changes_rejected(builder, sources):
    parent_path, proposal_path, parent, _, _ = sources
    result = builder.build_search_queue(parent_path, proposal_path)
    assert result["source_sha256"] == {
        **parent["source_sha256"], "parent_queue": builder.PARENT_QUEUE_SHA256,
        "proposal": builder.PROPOSAL_SHA256,
        "builder": hashlib.sha256(Path(builder.__file__).read_bytes()).hexdigest(),
    }
    proposal_path.write_bytes(proposal_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="proposal checksum"):
        builder.build_search_queue(parent_path, proposal_path)
    parent_path.write_bytes(parent_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="parent queue checksum"):
        builder.build_search_queue(parent_path, proposal_path)


@pytest.mark.parametrize("change", ["count", "split", "scope", "frame", "provenance"])
def test_malformed_or_mis_scoped_inputs_fail_closed(builder, sources, monkeypatch, change):
    parent_path, proposal_path, parent, _, _ = sources
    if change == "count":
        parent["entries"].pop()
    elif change == "split":
        parent["split"] = "test"
    elif change == "scope":
        parent["entries"][0]["search_scope"] = {}
    elif change == "frame":
        parent["entries"][0]["assets"][0]["bounds"] = [0, 0, 0, 1]
    elif change == "provenance":
        parent["source_sha256"]["ports"] = "d" * 64
    write_parent(parent_path, parent)
    parent_hash = hashlib.sha256(parent_path.read_bytes()).hexdigest()
    monkeypatch.setattr(builder, "PARENT_QUEUE_SHA256", parent_hash)
    proposal = json.loads(proposal_path.read_text())
    proposal["sources"][0]["sha256"] = parent_hash
    proposal_path.write_text(json.dumps(proposal))
    monkeypatch.setattr(builder, "PROPOSAL_SHA256",
                        hashlib.sha256(proposal_path.read_bytes()).hexdigest())
    with pytest.raises(ValueError):
        builder.build_search_queue(parent_path, proposal_path)


def test_cli_only_freezes_a_new_file_and_never_overwrites_parent(builder, sources, tmp_path, capsys):
    parent_path, proposal_path, _, _, _ = sources
    original = parent_path.read_bytes()
    output = tmp_path / "derived" / "queue.json"
    args = ["--parent-queue", str(parent_path), "--proposal", str(proposal_path),
            "--output", str(output)]
    assert builder.main(args) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["activated"] is False and receipt["extended_entries"] == 3
    envelope = json.loads(output.read_text())
    assert envelope["sha256"] == digest(envelope["manifest"])
    assert receipt["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert parent_path.read_bytes() == original
    with pytest.raises(FileExistsError):
        builder.main(args)
    with pytest.raises(FileExistsError):
        freeze_queue(envelope["manifest"], parent_path)
    assert parent_path.read_bytes() == original


def test_production_inputs_are_pinned(builder):
    assert builder.PARENT_QUEUE_SHA256 == (
        "35c92bca87c79d64833edde6db1870f0b00fd2cc54a0177857e150a84a33d5bc"
    )
    assert builder.PROPOSAL_SHA256 == (
        "a3bafdf405d49047eecb20abb6c942e4c32597082af2da54afed74971efdef25"
    )
