import copy
import json
from uuid import uuid4

import pytest
from shapely.geometry import box, mapping, shape
from test_review_queue import fixture  # noqa: F401

from puertogt.review import ReviewConflict
from puertogt.review_intake import preview_rectangle, projected_search_scope, record_first_pass
from puertogt.review_queue import (
    blind_entry,
    build_review_queue,
    digest,
    first_pass_exposure,
    freeze_queue,
    read_queue,
)


def resign(manifest):
    manifest["queue_id"] = "train-" + digest({k: v for k, v in manifest.items() if k != "queue_id"})[:20]
    return manifest


@pytest.fixture
def extended_queue(fixture):  # noqa: F811
    store, paths = fixture
    original = build_review_queue(store, *paths)
    manifest = copy.deepcopy(original)
    manifest["schema_version"] = "blind-train-queue-v2"
    entry = manifest["entries"][0]
    entry["zone_geometry"] = mapping(box(.004, .004, .006, .006))
    entry["assets"][0].update(bounds=[0, 0, .01, .01], wgs84_bounds=[0, 0, .01, .01])
    geometry = mapping(box(.001, .001, .009, .009))
    entry["search_scope"] = {
        "schema_version": "water-search-scope-v1", "radius_m": 500,
        "geometry": geometry, "geometry_sha256": digest(geometry),
        "reference_zone_sha256": digest(entry["zone_geometry"]),
        "source_sha256": "a" * 64, "frame_clipped": False,
        "observability": "not_measured",
    }
    return store, original, resign(manifest)


def test_frozen_v2_exposes_verified_search_scope_and_keeps_reference_zone(extended_queue, tmp_path):
    _, _, manifest = extended_queue
    path = tmp_path / "extended-queue.json"
    freeze_queue(manifest, path)
    loaded = read_queue(path)
    entry = blind_entry(loaded, "0")
    assert entry["search_scope"]["geometry"] == json.loads(json.dumps(mapping(box(.001, .001, .009, .009))))
    assert entry["zone_geometry"] == json.loads(json.dumps(mapping(box(.004, .004, .006, .006))))
    assert entry["search_scope"]["observability"] == "not_measured"


@pytest.mark.parametrize("change", ["outside_preview", "beyond_radius", "stale_geometry_hash",
                                   "stale_reference_hash", "nonfinite", "nonpolygon", "v1_scope"])
def test_scope_tampering_fails_even_with_recomputed_queue_hashes(extended_queue, tmp_path, change):
    _, _, manifest = extended_queue
    entry = manifest["entries"][0]
    scope = entry["search_scope"]
    if change == "outside_preview":
        # Inside the claimed radius, but beyond this entry's available preview.
        entry["assets"][0]["bounds"] = [0, 0, .008, .01]
    elif change == "beyond_radius":
        scope["geometry"] = mapping(box(.0001, .0001, .0099, .0099))
        scope["geometry_sha256"] = digest(scope["geometry"])
    elif change == "stale_geometry_hash":
        scope["geometry"] = mapping(box(.002, .002, .008, .008))
    elif change == "stale_reference_hash":
        entry["zone_geometry"] = mapping(box(.0045, .0045, .0055, .0055))
    elif change == "nonfinite":
        scope["geometry"] = {"type": "Polygon", "coordinates": [[[.001, .001], [float("nan"), .001], [.009, .009], [.001, .001]]]}
    elif change == "nonpolygon":
        scope["geometry"] = {"type": "Point", "coordinates": [.005, .005]}
        scope["geometry_sha256"] = digest(scope["geometry"])
    else:
        manifest["schema_version"] = "blind-train-queue-v1"
    path = tmp_path / "tampered-queue.json"
    if change == "nonfinite":
        path.write_text(json.dumps({"manifest": manifest, "sha256": "0" * 64}))
    else:
        resign(manifest)
        path.write_text(json.dumps({"manifest": manifest, "sha256": digest(manifest)}))
    with pytest.raises(ValueError):
        read_queue(path)


def submission_for(manifest, *, candidates=None, area=None):
    entry = manifest["entries"][0]
    return {"queue_id": manifest["queue_id"], "inspection_id": str(uuid4()),
            "observation_id": entry["observation_id"], "author": "Test reviewer",
            "reviewer_kind": "human_visual_review", "decision": "candidates",
            "candidates": candidates if candidates is not None else [mapping(box(.007, .004, .008, .005))],
            "reviewed_area": area if area is not None else entry["search_scope"]["geometry"],
            "note": "Visible candidate; activity has not been established", "elapsed_seconds": 30}


def test_margin_findings_are_preserved_as_separate_scope_with_immutable_replay(extended_queue):
    store, _, manifest = extended_queue
    body = submission_for(manifest, candidates=[mapping(box(.007, .004, .008, .005)),
                                              mapping(box(.0045, .0045, .0055, .0055))])
    result = record_first_pass(store, manifest, body)
    assert result["candidate_locations"] == ["search_margin", "reference_zone"]
    assert result["scope"]["observability"] == "not_measured"
    assert result["is_training_label"] is False and result["is_independent_validation"] is False
    path = store.data_dir / "review-intake" / manifest["queue_id"] / f"{body['inspection_id']}.json"
    first = path.read_bytes()
    record = json.loads(first)
    assert record["schema"] == "puertogt-first-pass-v1"
    assert record["candidate_locations"] == ["search_margin", "reference_zone"]
    assert record["scope"]["reference_zone_sha256"] == manifest["entries"][0]["search_scope"]["reference_zone_sha256"]
    assert record_first_pass(store, manifest, body) == result
    with pytest.raises(ReviewConflict):
        record_first_pass(store, manifest, {**body, "note": "Changed finding"})
    assert path.read_bytes() == first
    assert [item["observation_id"] for item in first_pass_exposure(store.data_dir)] == ["0"]


def test_drawing_can_inspect_the_margin_and_classifies_centers_on_the_server(extended_queue):
    store, _, manifest = extended_queue
    request = {"queue_id": manifest["queue_id"], "observation_id": "0", "asset_id": "0-preview",
               "rectangle": [0, 0, 1, 1], "kind": "area"}
    area = preview_rectangle(store, manifest, request)
    assert shape(area["geometry"]).equals(box(.001, .001, .009, .009))
    assert shape(area["pixel_geometry"]).bounds == pytest.approx((.1, .1, .9, .9))
    margin = preview_rectangle(store, manifest, {**request, "kind": "candidate", "rectangle": [.7, .5, .8, .6]})
    assert margin["location"] == "search_margin"
    center = preview_rectangle(store, manifest, {**request, "kind": "candidate", "rectangle": [.45, .45, .55, .55]})
    assert center["location"] == "reference_zone"
    with pytest.raises(ValueError, match="search scope"):
        preview_rectangle(store, manifest, {**request, "kind": "candidate", "rectangle": [.92, .45, .98, .55]})
    entry = blind_entry(manifest, "0")
    assert shape(projected_search_scope(entry, entry["assets"][0])).bounds == pytest.approx((.1, .1, .9, .9))


@pytest.mark.parametrize("case", ["outside_search", "outside_inspection", "inspection_outside_search", "legacy_v1"])
def test_direct_submission_cannot_bypass_declared_inspection_bounds(extended_queue, case):
    store, _, manifest = extended_queue
    body = submission_for(manifest)
    if case == "outside_search":
        body["candidates"] = [mapping(box(.0092, .004, .0098, .005))]
    elif case == "outside_inspection":
        body["reviewed_area"] = mapping(box(.004, .004, .006, .006))
    elif case == "inspection_outside_search":
        body["reviewed_area"] = mapping(box(0, 0, .01, .01))
    else:
        manifest["schema_version"] = "blind-train-queue-v1"
        del manifest["entries"][0]["search_scope"]
        resign(manifest)
        body["queue_id"] = manifest["queue_id"]
        body["reviewed_area"] = manifest["entries"][0]["zone_geometry"]
    with pytest.raises(ValueError):
        record_first_pass(store, manifest, body)
    assert first_pass_exposure(store.data_dir) == []


def test_scope_must_fit_every_frozen_preview(extended_queue, tmp_path):
    _, _, manifest = extended_queue
    entry = manifest["entries"][0]
    entry["assets"].append({**entry["assets"][0], "asset_id": "other-preview", "bounds": [0, 0, .005, .01]})
    resign(manifest)
    path = tmp_path / "two-previews.json"
    path.write_text(json.dumps({"manifest": manifest, "sha256": digest(manifest)}))
    with pytest.raises(ValueError, match="preview"):
        read_queue(path)
