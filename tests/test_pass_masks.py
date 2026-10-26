import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

from puertogt.jobs import JobQueue, RuntimeLimits
from puertogt.pass_coverage import pass_coverage_report
from puertogt.pass_masks import build_pass_union_cache
from puertogt.store import Store


def fixture(tmp_path):
    store = Store(tmp_path / "catalog.sqlite")
    queue = JobQueue(store, runtime=RuntimeLimits(reserve_bytes=0))
    ports = tmp_path / "ports.geojson"
    geometry = mapping(box(-91, 13, -90.9, 13.1))
    ports.write_text(
        json.dumps(
            {
                "features": [
                    {
                        "geometry": geometry,
                        "properties": {
                            "port_id": "prq",
                            "port_name": "Quetzal",
                            "zone_id": "prq_canal",
                        },
                    }
                ]
            }
        )
    )
    for index in (0, 1):
        key = (
            f"S1A:IW:GRDH:1SDV:20250101T0000{index * 25:02}:"
            f"20250101T0000{index * 25 + 24:02}:055960:06D7B8"
        )
        job = queue.enqueue(
            dict(
                scene_id=key,
                acquisition_key=key,
                sensor="sentinel-1",
                product_level="IW_GRDH_1SDV",
                captured_at=f"2025-01-01T00:00:{index * 25:02}Z",
                footprint=geometry,
                port_ids=["prq"],
            ),
            "prq_canal",
            "r1",
            port_id="prq",
        )
        with store.connect() as tx:
            observation_id = tx.execute(
                "select observation_id from jobs where job_id=?", (job,)
            ).fetchone()[0]
        path = store.data_dir / "assets" / observation_id / "observable.tif"
        path.parent.mkdir(parents=True)
        mask = np.zeros((10, 10), dtype=np.uint8)
        mask[:, index * 5 : (index + 1) * 5] = 1
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=10,
            height=10,
            count=1,
            dtype="uint8",
            crs="EPSG:4326",
            transform=from_origin(-91, 13.1, 0.01, 0.01),
            nodata=0,
        ) as dst:
            dst.write(mask, 1)
        with store.transaction() as tx:
            tx.execute(
                "update observations set status='partial',observable_fraction=.5 "
                "where observation_id=?",
                (observation_id,),
            )
            store.register_asset(
                path.relative_to(store.data_dir),
                observation_id=observation_id,
                metadata={"mime_type": "image/tiff", "role": "mask"},
                connection=tx,
            )
    return store, ports


def test_same_pass_union_recovers_complementary_pixels_without_double_counting(tmp_path):
    store, ports = fixture(tmp_path)
    path = tmp_path / "unions.json"
    result = build_pass_union_cache(store, ports, path)
    assert len(result["entries"]) == 1
    assert result["entries"][0]["observable_fraction"] == 1
    assert result["entries"][0]["baseline_max_fraction"] == 0.5
    report = pass_coverage_report(store, ports, mask_union_cache=path)
    item = next(i for i in report["items"] if i["sensor"] == "sentinel-1")
    assert item["catalogued_passes"] == 1
    assert item["sufficiently_observed_passes"] == 1
    assert item["conservative_sufficiently_observed_passes"] == 0
    assert report["source"]["mask_union_entries_applied"] == 1


def test_mask_union_cache_rejects_changed_asset_and_zone(tmp_path):
    store, ports = fixture(tmp_path)
    cache = tmp_path / "unions.json"
    build_pass_union_cache(store, ports, cache)
    with store.transaction() as tx:
        tx.execute("UPDATE assets SET sha256=?", ("1" * 64,))
    report = pass_coverage_report(store, ports, mask_union_cache=cache)
    assert report["source"]["mask_union_entries_applied"] == 0
    assert all(i["sufficiently_observed_passes"] == 0 for i in report["items"])
    ports.write_text(ports.read_text() + "\n")
    report = pass_coverage_report(store, ports, mask_union_cache=cache)
    assert report["source"]["mask_union_cache_status"] == "geometry_changed"


def test_union_builder_reports_modified_mask_bytes_instead_of_using_them(tmp_path):
    store, ports = fixture(tmp_path)
    path = next((store.data_dir / "assets").glob("*/observable.tif"))
    path.write_bytes(path.read_bytes() + b"changed")
    result = build_pass_union_cache(store, ports, tmp_path / "unions.json")
    assert result["entries"] == []
    assert result["errors"][0]["error_code"] == "ValueError"
    assert "checksum" in result["errors"][0]["message"]


def test_missing_cache_is_an_explicit_conservative_fallback(tmp_path):
    store, ports = fixture(tmp_path)
    report = pass_coverage_report(store, ports, mask_union_cache=Path(tmp_path / "absent.json"))
    assert report["source"]["mask_union_cache_status"] == "missing"
    assert report["source"]["mask_union_entries_applied"] == 0


@pytest.mark.parametrize("change", ["unusable", "rejected", "asset_removed"])
def test_review_or_asset_invalidation_prevents_cached_coverage(tmp_path, change):
    store, ports = fixture(tmp_path)
    cache = tmp_path / "unions.json"
    build_pass_union_cache(store, ports, cache)
    with store.transaction() as tx:
        if change == "unusable":
            tx.execute("UPDATE observations SET status='unusable'")
        elif change == "rejected":
            tx.execute("UPDATE observations SET review_status='rejected'")
        else:
            tx.execute("DELETE FROM assets")
    report = pass_coverage_report(store, ports, mask_union_cache=cache)
    assert report["source"]["mask_union_entries_applied"] == 0
    assert all(item["sufficiently_observed_passes"] == 0 for item in report["items"])
    rebuilt = build_pass_union_cache(store, ports, tmp_path / "rebuilt.json")
    assert rebuilt["entries"] == []
