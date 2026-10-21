import json
from contextlib import closing

import numpy as np
import pytest
import rasterio
from rasterio import Affine

from puertogt.contracts import RasterBundle
from puertogt.ml.history_evidence import freeze_history_evidence, retain_history_evidence
from puertogt.preprocess import S2_BANDS
from puertogt.store import Store, sha256_file


def evidence(tmp_path):
    store = Store(tmp_path / "catalog.sqlite")
    key = "S2A:MSIL1C:20250103T162701:R040:T15PYR"
    prepared = []
    for index, zone in enumerate(["reviewed", "other-zone"]):
        transform = Affine(10, 0, 500000 + index * 4480 - 320, 0, -10, 1504800)
        tile = {"tile_id": f"tile-{index}", "crs": "EPSG:32615", "size": 512,
                "margin": 32, "pixel_size": 10, "zone_ids": [zone],
                "core_bounds": [500000 + index * 4480, 1500000, 504480 + index * 4480, 1504480],
                "transform": list(transform)[:6]}
        root = tmp_path / "work" / tile["tile_id"]
        root.mkdir(parents=True)
        paths = {}
        for name in [*S2_BANDS, "observable"]:
            path = root / f"{name}.tif"
            with rasterio.open(path, "w", driver="GTiff", width=512, height=512,
                               count=1, dtype="uint16", crs=tile["crs"], transform=transform,
                               compress="deflate") as dst:
                dst.write(np.full((512, 512), index + 1, dtype="uint16"), 1)
            paths[name] = path
        bundle = RasterBundle(band_paths={n: paths[n] for n in S2_BANDS},
                              mask_paths={"observable": paths["observable"]},
                              crs=tile["crs"], transform=tuple(transform)[:6],
                              units={n: "DN_harmonized" for n in S2_BANDS},
                              sensor="sentinel-2", product_level="L1C", preprocessing_version="ai2-s2-v1",
                              metadata={"acquisition_key": key,
                                        "history_file_hashes": {n: sha256_file(p) for n, p in paths.items()}})
        prepared.append((tile, bundle))
    item = {"scene": {"acquisition_key": key, "sensor": "sentinel-2", "captured_at": "2025-01-03T16:27:01Z"},
            "tiles": [tile for tile, _ in prepared]}
    return store, item, prepared, [{"acquisition_key": key, "zone_id": "reviewed"}]


def test_freeze_copies_all_acquisition_tiles_including_other_zones_and_halos(tmp_path):
    store, item, prepared, samples = evidence(tmp_path)
    pointer = retain_history_evidence(store, item, prepared, sources={"pinned": True})
    assert pointer == retain_history_evidence(store, item, prepared, sources={"pinned": True})
    # The materialization cache can be discarded without losing exact evidence.
    import shutil
    shutil.rmtree(tmp_path / "work")
    destination = tmp_path / "dataset"
    frozen = freeze_history_evidence(store, destination, samples)
    assert frozen["version"] == "history-tiles-v1"
    acquisition = frozen["acquisitions"][0]
    assert acquisition["pass_id"] == "S2A:20250103T162701:R040"
    assert acquisition["expected_tile_ids"] == ["tile-0", "tile-1"]
    assert len(acquisition["tiles"]) == 2
    assert acquisition["provenance"]["original_halo_hash_available"] is True
    for entry in acquisition["tiles"]:
        for relative, digest in entry["file_sha256"].items():
            assert not relative.startswith("/")
            assert sha256_file(destination / relative) == digest
            with rasterio.open(destination / relative) as src:
                assert src.shape == (512, 512)
                assert src.read(1)[0, 0] in {1, 2}
    with closing(store.connect()) as tx:
        assert tx.execute("select count(*) from assets").fetchone()[0] == 21


@pytest.mark.parametrize("damage", ["missing", "changed", "incomplete-plan"])
def test_freeze_fails_closed_on_any_missing_halo_changed_hash_or_incomplete_plan(tmp_path, damage):
    store, item, prepared, samples = evidence(tmp_path)
    pointer = retain_history_evidence(store, item, prepared, sources={"pinned": True})
    manifest_path = store.asset_path(pointer["manifest_asset_id"])
    manifest = json.loads(manifest_path.read_text())
    target = store.data_dir / next(iter(manifest["tiles"][1]["file_sha256"]))
    if damage == "missing":
        target.unlink()
    elif damage == "changed":
        with target.open("ab") as out:
            out.write(b"changed")
    else:
        # Even an internally registered partial manifest must fail completeness.
        manifest["tiles"].pop()
        manifest_path.write_text(json.dumps(manifest))
        with store.transaction() as tx:
            tx.execute("update assets set sha256=?,size_bytes=? where asset_id=?",
                       (sha256_file(manifest_path), manifest_path.stat().st_size, pointer["manifest_asset_id"]))
    with pytest.raises((ValueError, FileNotFoundError)):
        freeze_history_evidence(store, tmp_path / "dataset", samples)
    assert not (tmp_path / "dataset" / "history").exists()


def test_retention_refuses_missing_plan_tile_before_registering_partial_evidence(tmp_path):
    store, item, prepared, _ = evidence(tmp_path)
    with pytest.raises(ValueError, match="(?i)complete.*plan|plan.*complete"):
        retain_history_evidence(store, item, prepared[:1], sources={"pinned": True})
    with closing(store.connect()) as tx:
        assert tx.execute("select count(*) from assets").fetchone()[0] == 0


def test_no_sample_is_silently_omitted_and_reconstruction_provenance_is_explicit(tmp_path):
    store, item, prepared, samples = evidence(tmp_path)
    retain_history_evidence(store, item, prepared, sources={"pinned": True}, provenance={
        "kind": "reconstructed_from_pinned_sources", "original_halo_hash_available": False,
        "core_pixel_match_verified": True})
    with pytest.raises(ValueError, match="missing.*evidence|evidence.*missing"):
        freeze_history_evidence(store, tmp_path / "dataset", samples + [{"acquisition_key": "unknown"}])
    assert not (tmp_path / "dataset" / "history").exists()
    output = freeze_history_evidence(store, tmp_path / "dataset", samples)
    assert output["acquisitions"][0]["provenance"]["original_halo_hash_available"] is False


def test_retry_cannot_infer_from_different_bytes_than_retained_evidence(tmp_path):
    store, item, prepared, _ = evidence(tmp_path)
    retain_history_evidence(store, item, prepared, sources={"pinned": True})
    bundle = prepared[0][1]
    path = bundle.band_paths["B04"]
    with rasterio.open(path, "r+") as dst:
        data = dst.read(1)
        data[0, 0] += 1
        dst.write(data, 1)
    bundle.metadata["history_file_hashes"]["B04"] = sha256_file(path)
    with pytest.raises(ValueError, match="retry.*bytes|bytes.*retry"):
        retain_history_evidence(store, item, prepared, sources={"pinned": True})


def test_frozen_history_rejects_symlink_and_path_escape(tmp_path):
    store, item, prepared, samples = evidence(tmp_path)
    pointer = retain_history_evidence(store, item, prepared, sources={"pinned": True})
    manifest = json.loads(store.asset_path(pointer["manifest_asset_id"]).read_text())
    path = store.data_dir / next(iter(manifest["tiles"][0]["file_sha256"]))
    backup = tmp_path / "original.tif"
    path.rename(backup)
    path.symlink_to(backup)
    with pytest.raises(ValueError, match="Symlink|symlink"):
        freeze_history_evidence(store, tmp_path / "dataset", samples)


def test_reconstruction_requires_exact_published_core_pixels(tmp_path):
    from datetime import datetime, timezone

    from puertogt.contracts import Scene
    from puertogt.history import mosaic_zone, verify_published_history_cores
    from puertogt.jobs import JobQueue

    store, item, prepared, _ = evidence(tmp_path)
    scene = Scene(scene_id="test", acquisition_key=item["scene"]["acquisition_key"],
                  sensor="sentinel-2", product_level="L1C", captured_at=datetime.now(timezone.utc),
                  footprint={"type": "Polygon", "coordinates": [[[-91, 13], [-90, 13], [-90, 14], [-91, 13]]]})
    item["zones"] = [{"properties": {"zone_id": zone}} for zone in ["reviewed", "other-zone"]]
    for feature in item["zones"]:
        zone = feature["properties"]["zone_id"]
        queue = JobQueue(store)
        job = queue.enqueue(scene, zone, "recipe")
        observation = queue.get_job(job)["observation_id"]
        with store.transaction() as tx:
            tx.execute("update observations set model_id='test-history-full-v1' where observation_id=?", (observation,))
        mosaic = mosaic_zone(prepared, zone, tmp_path / "published" / zone)
        for name, path in {**mosaic.band_paths, **mosaic.mask_paths}.items():
            store.register_asset(path.relative_to(store.data_dir), observation_id=observation,
                                 metadata={"role": "numeric" if name in mosaic.band_paths else "mask"})
    result = verify_published_history_cores(store, item, prepared, tmp_path / "compare")
    assert result["core_pixel_match_verified"] is True
    assert result["verified_mosaic_files"] == 20
    path = prepared[0][1].band_paths["B04"]
    with rasterio.open(path, "r+") as dst:
        data = dst.read(1)
        data[32, 32] += 1
        dst.write(data, 1)
    with pytest.raises(ValueError, match="core pixels differ"):
        verify_published_history_cores(store, item, prepared, tmp_path / "compare")
