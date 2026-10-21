from contextlib import closing

from puertogt.jobs import JobQueue, RuntimeLimits
from puertogt.store import Store


def scene(key, capture="2026-09-02T10:00:00Z"):
    return {"scene_id": key, "acquisition_key": key, "sensor": "sentinel-2", "product_level": "L1C",
            "captured_at": capture, "port_ids": ["quetzal"], "source_assets": {}}


def test_repeated_interval_and_late_publication_only_add_new_observations(tmp_path):
    store = Store(tmp_path / "catalog.sqlite")
    queue = JobQueue(store, runtime=RuntimeLimits(reserve_bytes=0))
    zones = {"quetzal": ["prq_muelles", "prq_fondeo"]}
    first = queue.enqueue_many([scene("take-a")], zones, "s2-v1")
    second = queue.enqueue_many([scene("take-a")], zones, "s2-v1")
    assert first == second
    assert queue.count_jobs() == 2
    third = queue.enqueue_many([scene("take-a"), scene("late-take", "2026-09-01T10:00:00Z")], zones, "s2-v1")
    assert len(set(third)) == 4
    assert queue.count_jobs() == 4
    job = queue.claim("worker")
    queue.complete(job["job_id"], job["lease_token"], status="unusable", quality_flags=["clouds"])
    queue.enqueue_many([scene("take-a"), scene("late-take", "2026-09-01T10:00:00Z")], zones, "s2-v1")
    with closing(store.connect()) as connection:
        assert connection.execute("SELECT count(*) FROM observations").fetchone()[0] == 4
        assert connection.execute("SELECT count(*) FROM observations WHERE status='unusable'").fetchone()[0] == 1
    assert len(queue.exceptions()) == 3
    queue.close()
