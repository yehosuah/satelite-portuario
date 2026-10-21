import json

from puertogt.coverage import coverage_report
from puertogt.jobs import JobQueue, RuntimeLimits
from puertogt.store import Store


def test_each_recipe_denominator_includes_unprocessed_acquisitions(tmp_path):
    store = Store(tmp_path / "catalog.sqlite")
    queue = JobQueue(store, runtime=RuntimeLimits(reserve_bytes=0))
    footprint = {
        "type": "Polygon",
        "coordinates": [[[-91, 13], [-90, 13], [-90, 14], [-91, 14], [-91, 13]]],
    }
    for number in (1, 2):
        scene = dict(
            scene_id=str(number),
            acquisition_key=str(number),
            sensor="sentinel-2",
            product_level="L1C",
            captured_at=f"2025-01-0{number}T00:00:00Z",
            footprint=footprint,
            port_ids=["prq"],
        )
        queue.enqueue(scene, "prq_muelles", "r1", port_id="prq")
        if number == 1:
            queue.enqueue(scene, "prq_muelles", "r2", port_id="prq")
    with store.transaction() as tx:
        tx.execute("UPDATE observations SET status='usable',detection_count=0")
    ports = tmp_path / "ports.geojson"
    ports.write_text(
        json.dumps(
            {
                "features": [
                    {
                        "geometry": footprint,
                        "properties": {"port_id": "prq", "zone_id": "prq_muelles"},
                    }
                ]
            }
        )
    )
    report = coverage_report(store, ports)
    counts = {item["recipe_version"]: item for item in report["items"]}
    assert counts["r1"]["catalogued"] == 2
    assert counts["r1"]["usable"] == 2
    assert counts["r2"]["catalogued"] == 2
    assert counts["r2"]["usable"] == 1
    assert counts["r2"]["pending"] == 1
    assert report["exceptions"][0]["acquisition_key"] == "2"
