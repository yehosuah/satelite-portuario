from datetime import datetime, timedelta, timezone

import pytest

from puertogt.jobs import JobQueue, LeaseLost, RuntimeLimits
from puertogt.store import Store


@pytest.fixture
def scene():
    return {
        "scene_id": "s2-safe-1",
        "acquisition_key": "s2-take-1",
        "sensor": "sentinel-2",
        "product_level": "L1C",
        "captured_at": "2026-09-02T10:00:00Z",
        "published_at": "2026-09-02T11:00:00Z",
        "discovered_at": "2026-09-03T00:00:00Z",
        "footprint": {"type": "Polygon", "coordinates": []},
        "source_assets": {},
        "port_ids": ["quetzal"],
        "provider": "fixture",
    }


@pytest.fixture
def clock():
    return [datetime(2026, 10, 20, tzinfo=timezone.utc)]


@pytest.fixture
def queue(tmp_path, clock):
    queue = JobQueue(
        Store(tmp_path / "catalog.sqlite"),
        runtime=RuntimeLimits(reserve_bytes=0),
        clock=lambda: clock[0],
    )
    yield queue
    queue.close()


def test_retry_keeps_one_job_and_separate_dates(queue, scene):
    first = queue.enqueue(scene, "prq_muelles", "s2-v1")
    second = queue.enqueue({**scene, "scene_id": "same-take-cog"}, "prq_muelles", "s2-v1")
    assert first == second
    assert queue.count_jobs() == 1
    job = queue.claim("worker-a")
    queue.complete(first, job["lease_token"], status="usable", observable_fraction=1)
    observation = queue.store.get_observation(job["observation_id"])
    assert observation["detection_count"] == 0
    assert observation["captured_at"] == "2026-09-02T10:00:00.000000Z"
    assert observation["processed_at"] == "2026-10-20T00:00:00.000000Z"
    with queue.store.connect() as connection:
        acquisition = connection.execute("SELECT * FROM acquisitions").fetchone()
        assert acquisition["published_at"] == "2026-09-02T11:00:00.000000Z"
        assert acquisition["discovered_at"] == "2026-09-03T00:00:00.000000Z"


def test_expired_lease_cannot_steal_from_active_process(queue, scene, clock):
    queue.enqueue(scene, "prq_muelles", "s2-v1")
    job = queue.claim("worker-a")
    clock[0] += timedelta(hours=1)
    challenger = JobQueue(queue.store, runtime=queue.runtime, clock=lambda: clock[0])
    try:
        assert challenger.claim("worker-b") is None
        assert challenger.recover_expired() == 0
        queue.close()  # Mimic process exit: the OS releases this advisory lock.
        assert challenger.recover_expired() == 1
        recovered = challenger.claim("worker-b")
        assert recovered["job_id"] == job["job_id"]
        with pytest.raises(LeaseLost):
            queue.complete(job["job_id"], job["lease_token"], status="usable")
        challenger.complete(recovered["job_id"], recovered["lease_token"], status="unusable")
        assert queue.store.get_observation(job["observation_id"])["detection_count"] is None
    finally:
        challenger.close()


def test_transient_retry_waits_then_input_failure_stops(queue, scene, clock):
    job_id = queue.enqueue(scene, "prq_muelles", "s2-v1")
    first = queue.claim("worker")
    queue.fail(job_id, first["lease_token"], "network timeout", transient=True)
    assert queue.claim("worker") is None
    clock[0] += timedelta(minutes=5)
    second = queue.claim("worker")
    assert second["attempts"] == 2
    queue.fail(job_id, second["lease_token"], "L2A cannot replace L1C", transient=False)
    assert queue.get_job(job_id)["status"] == "input_failed"
    assert queue.claim("worker") is None


def test_resource_pause_mid_job_is_resumable_without_burning_attempts(queue, scene):
    job_id = queue.enqueue(scene, "prq_muelles", "r1")
    claimed = queue.claim("worker")
    queue.pause(job_id, claimed["lease_token"], "insufficient_disk")
    assert queue.get_job(job_id)["status"] == "paused"
    assert queue.get_job(job_id)["attempts"] == 0
    queue.resume(job_id)
    retried = queue.claim("worker")
    assert retried["job_id"] == job_id
    assert retried["attempts"] == 1


def test_insufficient_disk_and_quota_pause_without_losing_work(tmp_path, clock, scene):
    store = Store(tmp_path / "catalog.sqlite")
    queue = JobQueue(
        store,
        runtime=RuntimeLimits(reserve_bytes=100),
        clock=lambda: clock[0],
        free_bytes=lambda _: 99,
    )
    try:
        job_id = queue.enqueue(
            scene, "prq_muelles", "s2-v1", quota_key="provider-requests", quota_units=2
        )
        assert queue.claim("worker") is None
        assert queue.get_job(job_id)["error_code"] == "insufficient_disk"
        assert queue.get_job(job_id)["attempts"] == 0
        queue.free_bytes = lambda _: 1000
        store.configure_quota("provider-requests", "2026-10", 1)
        queue.resume(job_id)
        assert queue.claim("worker") is None
        assert queue.get_job(job_id)["error_code"] == "quota_exhausted"
        store.configure_quota("provider-requests", "2026-10", 3)
        queue.resume(job_id)
        running = queue.claim("worker")
        assert running["job_id"] == job_id
    finally:
        queue.close()


def test_interrupted_write_never_registers_partial_asset(queue, scene, tmp_path, clock):
    queue.enqueue(scene, "prq_muelles", "s2-v1")
    job = queue.claim("worker")

    def interrupted(chunks):
        yield b"first half"
        raise OSError("disk interrupted")

    with pytest.raises(OSError, match="interrupted"):
        queue.publish_asset(job["job_id"], job["lease_token"], interrupted(None), "assets/crop.bin")
    assert not (tmp_path / "assets/crop.bin").exists()
    with queue.store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM assets").fetchone()[0] == 0
    asset_id = queue.publish_asset(
        job["job_id"], job["lease_token"], [b"all", b" data"], "assets/crop.bin"
    )
    assert queue.store.asset_path(asset_id).read_bytes() == b"all data"
    with pytest.raises(FileExistsError):
        queue.publish_asset(job["job_id"], job["lease_token"], [b"replacement"], "assets/crop.bin")
    assert queue.store.asset_path(asset_id).read_bytes() == b"all data"


def test_completion_and_predictions_roll_back_together(queue, scene):
    queue.enqueue(scene, "prq_muelles", "s2-v1")
    job = queue.claim("worker")
    with pytest.raises(ValueError):
        queue.complete(
            job["job_id"],
            job["lease_token"],
            status="unusable",
            detections=[{"geometry": {"type": "Point", "coordinates": [0, 0]}}],
        )
    assert queue.get_job(job["job_id"])["status"] == "running"
    queue.complete(
        job["job_id"],
        job["lease_token"],
        status="partial",
        observable_fraction=0.6,
        detections=[{"geometry": {"type": "Point", "coordinates": [0, 0]}, "detection_score": 0.8}],
    )
    assert queue.store.get_observation(job["observation_id"])["detection_count"] == 1


def test_reject_unsafe_asset_path(queue, scene):
    queue.enqueue(scene, "prq_muelles", "s2-v1")
    job = queue.claim("worker")
    with pytest.raises(ValueError):
        queue.publish_asset(job["job_id"], job["lease_token"], [b"data"], "../escaped.bin")


def test_interrupted_job_reuses_complete_evidence_and_preserves_contract_ids(queue, scene, clock):
    job_id = queue.enqueue(scene, "prq_muelles", "s2-v1")
    first = queue.claim("worker-a")
    asset = queue.publish_asset(
        job_id, first["lease_token"], b"complete evidence", "assets/crop.bin"
    )
    queue.close()
    clock[0] += timedelta(hours=1)
    retry = queue.claim("worker-b")
    assert (
        queue.publish_asset(job_id, retry["lease_token"], b"complete evidence", "assets/crop.bin")
        == asset
    )
    queue.complete(
        job_id,
        retry["lease_token"],
        detections=[
            {
                "prediction_id": "p1",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
                "detection_score": 0.9,
                "evidence_asset_ids": [asset],
            }
        ],
    )
    with queue.store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM assets").fetchone()[0] == 1
        import json

        assert json.loads(
            connection.execute("SELECT evidence_json FROM detections").fetchone()[0]
        ) == [asset]


def test_worker_claims_only_requested_recipe(queue, scene):
    optical = queue.enqueue(scene, "prq_muelles", "s2-v1")
    radar = queue.enqueue(
        {**scene, "scene_id": "s1-take", "acquisition_key": "s1-take", "sensor": "sentinel-1"},
        "prq_muelles",
        "s1-v1",
    )
    job = queue.claim("radar-worker", recipe_version="s1-v1")
    assert job["job_id"] == radar
    assert queue.get_job(optical)["status"] == "pending"


def test_recovery_adopts_complete_orphan_but_never_overwrites_other_bytes(queue, scene):
    job_id = queue.enqueue(scene, "prq_muelles", "s2-v1")
    job = queue.claim("worker")
    target = queue.store.data_dir / "assets/orphan.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"complete but transaction not committed")
    asset = queue.publish_asset(
        job_id, job["lease_token"], target.read_bytes(), "assets/orphan.bin"
    )
    assert queue.store.asset_path(asset) == target
    with pytest.raises(FileExistsError):
        queue.publish_asset(job_id, job["lease_token"], b"different", "assets/orphan.bin")
