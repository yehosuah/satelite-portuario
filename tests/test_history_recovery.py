import socket
import urllib.error
from datetime import datetime, timezone

import pytest
from shapely.geometry import box, mapping

from puertogt.contracts import Scene
from puertogt.history import HistoryLedger, HistoryLimits, run_history
from puertogt.jobs import JobQueue, RuntimeLimits
from puertogt.store import Store


def campaign(tmp_path):
    scene = Scene(
        scene_id="local-scene", acquisition_key="local-scene", sensor="sentinel-2",
        product_level="L1C", captured_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        footprint=mapping(box(-88.7, 15.6, -88.5, 15.8)), port_ids=["stc"],
    )
    plan = {"plan_id": "test", "protected_observation_ids": [], "acquisitions": [
        {"scene": scene.model_dump(mode="json"), "zones": [], "tiles": []},
    ]}
    ledger = HistoryLedger(tmp_path / "history", HistoryLimits(reserve_bytes=0))
    ledger.initialize_plan(plan)
    catalog = Store(tmp_path / "catalog.sqlite", tmp_path / "data")
    return catalog, plan, ledger


def test_local_missing_file_is_retryable_not_public_source_unavailable(tmp_path, monkeypatch):
    catalog, plan, ledger = campaign(tmp_path)

    def fail_with_local_file(*args, **kwargs):
        (ledger.directory / "PAUSE").write_text("Stop after the failing acquisition.\n")
        raise FileNotFoundError(2, "No such file or directory", ".pending-local")

    monkeypatch.setattr("puertogt.history.materialize", fail_with_local_file)
    result = run_history(catalog, plan, ledger, {}, quality_models=tmp_path / "quality")

    assert result["states"] == {"retry": 1}


def test_explicit_frozen_context_absence_remains_unavailable(tmp_path, monkeypatch):
    catalog, plan, ledger = campaign(tmp_path)
    plan["acquisitions"][0]["planning_error"] = "No same-track public context 30–90 days before acquisition"
    with ledger.store.transaction() as tx:
        from puertogt.store import json_text
        tx.execute("UPDATE history_acquisitions SET plan_json=?", (json_text(plan["acquisitions"][0]),))

    def unexpected_materialization(*args, **kwargs):
        raise AssertionError("A frozen planning error must not download substitute pixels")

    monkeypatch.setattr("puertogt.history.materialize", unexpected_materialization)
    result = run_history(catalog, plan, ledger, {}, quality_models=tmp_path / "quality")

    assert result["states"] == {"unavailable": 1}


def test_dns_failure_before_response_does_not_consume_download_budget(tmp_path, monkeypatch):
    from puertogt.history import _RangeSource

    _, _, ledger = campaign(tmp_path)
    calls = []

    def dns_failure(*args, **kwargs):
        calls.append(1)
        # Every individual request still reserves its full bound before I/O.
        assert ledger.summary()["download_bytes_reserved"] == 4
        raise urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, "DNS unavailable"))

    monkeypatch.setattr("puertogt.history.urllib.request.urlopen", dns_failure)
    monkeypatch.setattr("puertogt.history.time.sleep", lambda seconds: None)
    source = _RangeSource({"url": "https://example.org/a.tif", "size": 8},
                          ledger, "local-scene", tmp_path / "cache")

    with pytest.raises(urllib.error.URLError):
        source.fetch(0, 4)

    assert len(calls) == 3
    reopened = HistoryLedger(ledger.directory, ledger.limits)
    assert reopened.summary()["download_bytes_reserved"] == 0


def test_dns_refund_receipt_is_durable_and_cannot_release_another_reservation(tmp_path):
    _, _, ledger = campaign(tmp_path)
    first = ledger.reserve_download("local-scene", 4)
    ledger.reserve_download("local-scene", 8)

    assert ledger.refund_dns_reservation(first) is True
    reopened = HistoryLedger(ledger.directory, ledger.limits)
    assert reopened.refund_dns_reservation(first) is False
    with pytest.raises(ValueError, match="Unknown"):
        reopened.refund_dns_reservation("unknown-receipt")
    assert reopened.summary()["download_bytes_reserved"] == 8


@pytest.mark.parametrize("failure", [
    OSError("Connection broke after receiving some bytes"),
    urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, "DNS error after response began")),
])
def test_interrupted_response_keeps_conservative_download_reservations(tmp_path, monkeypatch, failure):
    from puertogt.history import _RangeSource

    _, _, ledger = campaign(tmp_path)

    class InterruptedResponse:
        status = 206
        headers = {"Content-Range": "bytes 0-3/8"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            raise failure

    monkeypatch.setattr("puertogt.history.urllib.request.urlopen", lambda *args, **kwargs: InterruptedResponse())
    monkeypatch.setattr("puertogt.history.time.sleep", lambda seconds: None)
    source = _RangeSource({"url": "https://example.org/a.tif", "size": 8},
                          ledger, "local-scene", tmp_path / "cache")
    with pytest.raises(OSError):
        source.fetch(0, 4)
    assert ledger.summary()["download_bytes_reserved"] == 12


def failed_publication(tmp_path):
    catalog, plan, ledger = campaign(tmp_path)
    queue = JobQueue(catalog, runtime=RuntimeLimits(reserve_bytes=0))
    scene = plan["acquisitions"][0]["scene"]
    finished_id = queue.enqueue(scene, "finished-zone", "test", port_id="stc")
    finished = queue.claim("fixture")
    queue.complete(finished_id, finished["lease_token"], status="usable", observable_fraction=1)
    failed_id = queue.enqueue(scene, "failed-zone", "test", port_id="stc")
    failed = queue.claim("fixture")
    relative = f"assets/{failed['observation_id']}/B02.tif"
    queue.publish_asset(failed_id, failed["lease_token"], b"retained pixels", relative)
    queue.fail(failed_id, failed["lease_token"], "FileNotFoundError", transient=False)
    ledger.claim()
    ledger.reserve_download(scene["acquisition_key"], 14)
    directory = catalog.data_dir / "assets" / failed["observation_id"]
    ledger.fail(scene["acquisition_key"], FileNotFoundError(
        2, "No such file or directory", str(directory / ".pending-fixture"), None,
        str(directory / "B03.tif")), permanent=True)
    (ledger.directory / "PAUSE").write_text("Paused for audited recovery.\n")
    return catalog, plan, ledger, queue, finished_id, failed_id, relative


def test_recovery_reopens_only_local_failure_without_resetting_attempts_or_assets(tmp_path):
    from puertogt.history import recover_local_publication

    catalog, plan, ledger, queue, finished_id, failed_id, relative = failed_publication(tmp_path)
    finished_before = queue.get_job(finished_id)
    failed_before = queue.get_job(failed_id)

    result = recover_local_publication(catalog, plan, ledger, "local-scene", reason="Local temporary publication failure verified")

    assert result["status"] == "complete"
    assert queue.get_job(failed_id)["status"] == "retry"
    assert queue.get_job(failed_id)["attempts"] == failed_before["attempts"] == 1
    assert queue.get_job(failed_id)["observation_id"] == failed_before["observation_id"]
    assert queue.get_job(finished_id) == finished_before
    assert queue.count_jobs() == 2
    assert ledger.summary()["states"] == {"retry": 1}
    assert ledger.summary()["download_bytes_reserved"] == 14
    assert (catalog.data_dir / relative).read_bytes() == b"retained pixels"
    assert recover_local_publication(catalog, plan, ledger, "local-scene", reason="Replay") == result


@pytest.mark.parametrize("phase,ledger_state", [("queue_reopened", "unavailable"), ("complete", "retry")])
def test_recovery_resumes_after_catalog_commit_without_duplicate_work(tmp_path, monkeypatch, phase, ledger_state):
    import puertogt.history as history

    catalog, plan, ledger, queue, finished_id, failed_id, _ = failed_publication(tmp_path)
    original = history._atomic_json

    def interrupt_after_catalog(path, value):
        if value.get("status") == phase:
            raise OSError("Simulated interruption after catalog commit")
        return original(path, value)

    monkeypatch.setattr(history, "_atomic_json", interrupt_after_catalog)
    with pytest.raises(OSError, match="Simulated interruption"):
        history.recover_local_publication(catalog, plan, ledger, "local-scene", reason="Audited local failure")
    assert queue.get_job(failed_id)["status"] == "retry"
    assert ledger.summary()["states"] == {ledger_state: 1}

    monkeypatch.setattr(history, "_atomic_json", original)
    history.recover_local_publication(catalog, plan, ledger, "local-scene", reason="Resume audited recovery")
    assert ledger.summary()["states"] == {"retry": 1}
    assert queue.get_job(failed_id)["attempts"] == 1
    assert queue.get_job(finished_id)["status"] == "succeeded"
    assert queue.count_jobs() == 2


def test_recovery_rejects_damaged_published_evidence_before_any_state_change(tmp_path):
    from puertogt.history import recover_local_publication

    catalog, plan, ledger, queue, _, failed_id, relative = failed_publication(tmp_path)
    (catalog.data_dir / relative).write_bytes(b"changed pixels")

    with pytest.raises(ValueError, match="checksum|evidence"):
        recover_local_publication(catalog, plan, ledger, "local-scene", reason="Audited local failure")

    assert queue.get_job(failed_id)["status"] == "input_failed"
    assert ledger.summary()["states"] == {"unavailable": 1}
