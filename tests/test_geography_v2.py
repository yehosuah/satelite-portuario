import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio import Affine
from shapely.geometry import box, shape

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "reports/coverage/geography-v2-candidates"


@pytest.fixture
def geography(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "geography_v2", ROOT / "scripts/build_geography_v2.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v2_preserves_v1_and_identities_corrects_land_and_closes_gap(geography):
    path = ROOT / "config/ports.geojson"
    before = path.read_bytes()
    original = json.loads(before)
    v2, audit, _ = geography.build_geography(path, CANDIDATES)
    assert path.read_bytes() == before
    assert v2["active"] is False
    assert v2["calibration_or_test_used"] is False
    assert len(v2["features"]) == 12
    assert {f["id"] for f in v2["features"]} == {f["id"] for f in original["features"]}
    changed = []
    for old, new in zip(original["features"], v2["features"], strict=True):
        assert shape(new["geometry"]).is_valid and shape(new["geometry"]).area > 0
        assert new["properties"]["active"] is False
        assert new["properties"]["version"] == geography.VERSION
        if old["geometry"] != new["geometry"]:
            changed.append(new["id"])
    assert set(changed) == {"prq_muelles", "stc_muelles", "pbr_muelles", "stc_fondeo"}
    fondeo = next(z for z in audit["zones"] if z["zone_id"] == "stc_fondeo")
    assert 0.65 < fondeo["v1_mapped_land_m2"] / fondeo["v1_area_m2"] < 0.66
    assert fondeo["v2_mapped_land_m2"] < 0.001
    assert fondeo["v2_area_m2"] < fondeo["v1_area_m2"]
    stc = next(p for p in audit["ports"] if p["port_id"] == "stc")
    assert 136 < stc["v1_quay_canal_gap_m"] < 138
    assert stc["v2_quay_canal_gap_m"] < 1e-5
    assert stc["v1_connector_covered_by_v2_fraction"] == pytest.approx(1)
    assert audit["v2_global_tile_ids"] == audit["v1_global_tile_ids"]
    for row in [*audit["zones"], *audit["ports"]]:
        assert all(value >= 0 for key, value in row.items() if key.endswith("_m2"))


def test_v2_additions_exclude_other_ports_and_preserve_overlap_within_rounding(geography):
    _, audit, _ = geography.build_geography(ROOT / "config/ports.geojson", CANDIDATES)
    assert sum(p["additional_water_m2"] for p in audit["ports"]) == pytest.approx(
        1048424.436, abs=2
    )
    for port in audit["ports"]:
        assert port["cross_port_overlap_m2"] < 0.001
        assert port["v2_within_port_overlap_m2"] == pytest.approx(
            port["v1_within_port_overlap_m2"], abs=1
        )


def test_changed_geographic_sources_are_rejected(geography, tmp_path):
    altered_v1 = tmp_path / "ports.geojson"
    altered_v1.write_bytes((ROOT / "config/ports.geojson").read_bytes() + b" ")
    with pytest.raises(ValueError, match="pinned V1"):
        geography.build_geography(altered_v1, CANDIDATES)
    candidate_dir = tmp_path / "candidates"
    shutil.copytree(CANDIDATES / "sources", candidate_dir / "sources")
    shutil.copyfile(CANDIDATES / "candidates.geojson", candidate_dir / "candidates.geojson")
    source = candidate_dir / "sources/osm-coastline-caribe.json"
    source.write_bytes(source.read_bytes() + b" ")
    with pytest.raises(ValueError, match="coastline source checksum"):
        geography.build_geography(ROOT / "config/ports.geojson", candidate_dir)
    receipt_path = candidate_dir / "sources/osm-coastline-caribe-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["sha256"] = geography.sha256_file(source)
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="Pinned coastline receipt changed"):
        geography.build_geography(ROOT / "config/ports.geojson", candidate_dir)


def test_build_receipt_is_reproducible_across_process_hash_seeds():
    code = (
        "import hashlib; from pathlib import Path; "
        "from build_geography_v2 import build_geography, encoded; "
        "v,a,c=build_geography(Path('config/ports.geojson'),"
        "Path('reports/coverage/geography-v2-candidates')); "
        "print(hashlib.sha256(encoded(a)).hexdigest())"
    )
    hashes = []
    for seed in ("1", "2"):
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
            env={
                **os.environ,
                "PYTHONPATH": f"{ROOT / 'src'}:{ROOT / 'scripts'}",
                "PYTHONHASHSEED": seed,
                "PYTHONWARNINGS": "ignore::DeprecationWarning",
            },
        )
        hashes.append(result.stdout.strip())
    assert hashes[0] == hashes[1]


def mask_record(geography, root, name, left, *, valid=1, observable=1):
    core_top = 4480
    grid = Affine(10, 0, left - 320, 0, -10, core_top + 320)
    paths, hashes = {}, {}
    for role, fill in (("valid", valid), ("observable", observable)):
        relative = f"{name}/{role}.tif"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=512,
            height=512,
            count=1,
            dtype="uint8",
            crs="EPSG:32616",
            transform=grid,
            compress="deflate",
        ) as dst:
            dst.write(np.full((512, 512), fill, dtype=np.uint8), 1)
        paths[role] = relative
        hashes[relative] = geography.sha256_file(path)
    return {
        "root": root,
        "acquisition_key": name,
        "captured_at": "2025-01-01T00:00:00Z",
        "entry": {
            "tile": {
                "tile_id": name,
                "size": 512,
                "margin": 32,
                "pixel_size": 10,
                "crs": "EPSG:32616",
                "core_bounds": [left, 0, left + 4480, core_top],
                "transform": list(grid)[:6],
            },
            "bundle": {
                "sensor": "sentinel-1",
                "crs": "EPSG:32616",
                "product_level": "GRDH",
                "preprocessing_version": "fixture-v1",
                "transform": list(grid)[:6],
                "metadata": {"acquisition_key": name},
                "mask_paths": paths,
            },
            "file_sha256": hashes,
        },
    }


def test_native_cores_deduplicate_slices_and_report_outside_frame(geography, tmp_path):
    one = mask_record(geography, tmp_path, "one", 0)
    two = mask_record(geography, tmp_path, "two", 4480)
    result = geography.native_mosaic([one, two, one], (0, 0, 9000, 4480))
    assert result["observable"].sum() == 2 * 448 * 448
    assert result["duplicate_core_pixels_removed"] == 448 * 448
    measured = geography.measure_area(box(0, 0, 9000, 4480), result)
    assert measured["outside_frame_pixel_count"] == 4 * 448
    assert measured["observable_pixel_count"] == 2 * 448 * 448
    assert measured["observable_fraction_of_framed_water_pixels"] == 1
    assert measured["observable_fraction_of_water_pixels"] < 1


def test_mask_corruption_grid_changes_and_invalid_observability_fail_closed(geography, tmp_path):
    record = mask_record(geography, tmp_path, "source", 0)
    changed_grid = copy.deepcopy(record)
    changed_grid["entry"]["tile"]["transform"][2] += 1
    with pytest.raises(ValueError, match="core grid changed"):
        geography.native_mosaic([changed_grid], (0, 0, 4480, 4480))
    invalid = mask_record(geography, tmp_path, "invalid", 0, valid=0, observable=1)
    with pytest.raises(ValueError, match="contains invalid pixels"):
        geography.native_mosaic([invalid], (0, 0, 4480, 4480))
    path = tmp_path / record["entry"]["bundle"]["mask_paths"]["observable"]
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        geography.native_mosaic([record], (0, 0, 4480, 4480))


@pytest.mark.parametrize("split", ["calibration", "test", None])
def test_pilot_rejects_any_non_train_manifest_before_opening_evidence(geography, tmp_path, split):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"split": split, "samples": [{"acquisition_key": "never-open"}]})
    )
    with pytest.raises(ValueError, match="TRAIN manifest"):
        geography.train_records([tmp_path])
