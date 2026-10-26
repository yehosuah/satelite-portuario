import json
from datetime import datetime, timezone

import pytest
from shapely.geometry import box, mapping

from puertogt.jobs import JobQueue, RuntimeLimits
from puertogt.pass_coverage import pass_coverage_report
from puertogt.store import Store


def setup_catalog(tmp_path):
    store = Store(tmp_path / "catalog.sqlite")
    queue = JobQueue(store, runtime=RuntimeLimits(reserve_bytes=0))
    geometry = mapping(box(-91, 13, -90.9, 13.1))
    ports = tmp_path / "ports.geojson"
    ports.write_text(
        json.dumps(
            {
                "features": [
                    {
                        "geometry": geometry,
                        "properties": {
                            "port_id": "prq",
                            "port_name": "Quetzal",
                            "zone_id": zone,
                        },
                    }
                    for zone in ("prq_muelles", "prq_fondeo")
                ]
            }
        )
    )
    return store, queue, ports, geometry


def add(queue, key, date, geometry, zone="prq_muelles", recipe="r1", metadata=None):
    queue.enqueue(
        dict(
            scene_id=key,
            acquisition_key=key,
            sensor="sentinel-1" if key.startswith("S1") else "sentinel-2",
            product_level="L1C",
            captured_at=date,
            footprint=geometry,
            port_ids=["prq"],
            metadata=metadata or {},
        ),
        zone,
        recipe,
        port_id="prq",
    )


def test_granules_and_reprocessing_do_not_invent_passes_or_observable_area(tmp_path):
    store, queue, ports, geometry = setup_catalog(tmp_path)
    for tile in ("T15PYR", "T15PYS"):
        for recipe in ("r1", "r2"):
            add(
                queue,
                f"S2A:MSIL1C:20250101T000000:R040:{tile}",
                "2025-01-01T00:00:00Z",
                geometry,
                recipe=recipe,
            )
    with store.transaction() as tx:
        tx.execute("UPDATE observations SET status='partial', observable_fraction=0.6")
    report = pass_coverage_report(store, ports)
    item = next(x for x in report["items"] if x["sensor"] == "sentinel-2")
    assert item["catalogued_acquisitions"] == 2
    assert item["catalogued_passes"] == 1
    assert item["sufficiently_observed_passes"] == 0
    zone = next(
        x for x in report["zones"] if x["zone_id"] == "prq_muelles" and x["sensor"] == "sentinel-2"
    )
    assert zone["mean_known_observable_fraction"] == 0.6
    assert report["source"]["observation_rows"] == 4


def test_full_port_requires_every_zone_and_combined_keeps_capture_times(tmp_path):
    store, queue, ports, geometry = setup_catalog(tmp_path)
    for zone in ("prq_muelles", "prq_fondeo"):
        add(queue, "S2A:MSIL1C:20250101T120000:R040:T15PYR", "2025-01-01T12:00:00Z", geometry, zone)
        add(
            queue,
            "S1A:IW:GRDH:1SDV:20250101T180000:20250101T180025:055960:06D7B8",
            "2025-01-01T18:00:00Z",
            geometry,
            zone,
        )
    add(queue, "S2A:MSIL1C:20250104T120000:R040:T15PYR", "2025-01-04T12:00:00Z", geometry)
    with store.transaction() as tx:
        tx.execute("UPDATE observations SET status='usable', observable_fraction=1")
    report = pass_coverage_report(
        store, ports, start="2025-01-01T00:00:00Z", end="2025-01-10T00:00:00Z"
    )
    item = next(x for x in report["items"] if x["sensor"] == "combined")
    assert item["catalogued_passes"] == 3
    assert item["sufficiently_observed_passes"] == 2
    assert item["sufficiently_observed_days"] == 1
    assert item["sufficiently_observed_intervals_days"]["median"] == 0.25
    assert item["sufficiently_observed_day_intervals_days"]["median"] is None
    assert item["sufficiently_observed_boundary_gaps_days"] == {
        "leading": 0.5,
        "trailing": 8.25,
    }


def test_radar_slices_same_absolute_orbit_are_one_pass(tmp_path):
    store, queue, ports, geometry = setup_catalog(tmp_path)
    for second in (0, 25):
        add(
            queue,
            f"S1A:IW:GRDH:1SDV:20250101T0000{second:02}:"
            f"20250101T0000{second + 24:02}:055960:06D7B8",
            f"2025-01-01T00:00:{second:02}Z",
            geometry,
        )
    add(
        queue,
        "S1C:IW:GRDH:1SDV:20250102T000000:20250102T000025:055960:06D7B8",
        "2025-01-02T00:00:00Z",
        geometry,
    )
    report = pass_coverage_report(store, ports, sensor="sentinel-1")
    assert report["items"][0]["catalogued_acquisitions"] == 3
    assert report["items"][0]["catalogued_passes"] == 2


def test_time_filters_context_exclusion_and_geographic_attribution(tmp_path):
    store, queue, ports, geometry = setup_catalog(tmp_path)
    for date, suffix, metadata in (
        ("2024-12-31T00:00:00Z", "a", {}),
        ("2025-01-01T00:00:00Z", "b", {"purpose": "auxiliary_context"}),
        ("2025-01-02T00:00:00Z", "c", {}),
        ("2025-01-03T00:00:00Z", "d", {}),
    ):
        add(queue, suffix, date, geometry, metadata=metadata)
    add(queue, "outside", "2025-01-02T00:00:00Z", mapping(box(-80, 10, -79, 11)))
    report = pass_coverage_report(store, ports, end="2025-01-03T00:00:00Z")
    item = next(x for x in report["items"] if x["sensor"] == "sentinel-2")
    assert item["catalogued_acquisitions"] == 1
    assert item["processed_passes"] == 0
    assert item["sufficiently_observed_passes"] == 0
    assert report["period"]["start"] == "2025-01-01T00:00:00Z"
    assert report["source"]["acquisition_rows"] == 2


def test_zero_fraction_unusable_and_missing_recipe_cannot_count_as_useful(tmp_path):
    store, queue, ports, geometry = setup_catalog(tmp_path)
    for zone in ("prq_muelles", "prq_fondeo"):
        add(queue, "a", "2025-01-01T00:00:00Z", geometry, zone, "r1")
    with store.transaction() as tx:
        tx.execute("UPDATE observations SET status='unusable', observable_fraction=0")
    report = pass_coverage_report(store, ports, recipe_version="r2")
    assert all(x["processed_passes"] == 0 for x in report["items"])
    report = pass_coverage_report(store, ports)
    assert all(
        x["processed_passes"] == 1
        for x in report["items"]
        if x["sensor"] in {"sentinel-2", "combined"}
    )
    assert all(x["sufficiently_observed_passes"] == 0 for x in report["items"])


def test_validation_and_stable_source_fingerprint(tmp_path):
    store, _, ports, _ = setup_catalog(tmp_path)
    a = pass_coverage_report(store, ports)
    b = pass_coverage_report(store, ports)
    assert a["source"] == b["source"]
    for kwargs in (
        {"start": datetime(2025, 1, 1)},
        {"min_observable_fraction": 0},
        {"sensor": "landsat"},
        {"port_id": "missing"},
        {
            "start": datetime(2025, 1, 2, tzinfo=timezone.utc),
            "end": datetime(2025, 1, 1, tzinfo=timezone.utc),
        },
    ):
        with pytest.raises(ValueError):
            pass_coverage_report(store, ports, **kwargs)
