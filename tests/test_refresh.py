import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from shapely.geometry import box, mapping

from puertogt.catalog import CatalogSearch, acquisition_key, normalize_products
from puertogt.history import HistoryLedger, HistoryLimits, create_selected_plan, file_lock
from puertogt.jobs import JobQueue
from puertogt.refresh import execute_refresh, freshness_report, plan_refresh
from puertogt.store import Store, sha256_file

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)


def product(day=23):
    stamp = f"202610{day:02}T161859"
    name = f"S2B_MSIL1C_{stamp}_N0512_R040_T15PYR_{stamp}.SAFE"
    return {
        "Id": str(day),
        "Name": name,
        "ContentDate": {
            "Start": f"2026-10-{day:02}T16:18:59Z",
            "End": f"2026-10-{day:02}T16:19:00Z",
        },
        "PublicationDate": f"2026-10-{day:02}T18:00:00Z",
        "GeoFootprint": mapping(box(-89, 15, -88, 16)),
        "S3Path": "/eodata/private-source-token",
        "Online": True,
        "Attributes": [],
    }


class Source:
    def __init__(self, products=(), *, fail_sensor=None):
        self.products, self.fail_sensor, self.calls = list(products), fail_sensor, []

    def search(self, sensor, geometry, start, end, *, as_of):
        self.calls.append((sensor, geometry, start, end, as_of))
        if sensor == self.fail_sensor:
            raise RuntimeError("https://host?token=private-source-token")
        products = self.products if sensor == "sentinel-2" else []
        return CatalogSearch(
            products,
            [{"url": "https://host?token=private-source-token", "count": len(products)}],
            len(products),
        )


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "data/catalog.sqlite", tmp_path / "data")
    features = [
        {
            "type": "Feature",
            "properties": {
                "port_id": port,
                "zone_id": port + "-zone",
                "port_name": port,
                "version": "v1",
            },
            "geometry": mapping(box(-88.624 + i * 0.01, 15.748, -88.623 + i * 0.01, 15.749)),
        }
        for i, port in enumerate(["prq", "pbr", "stc"])
    ]
    config = tmp_path / "config"
    config.mkdir()
    path = config / "ports.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return store, path, features


def plan(setup, source, *, run_id="a", **kwargs):
    store, path, _ = setup
    return plan_refresh(store, path, run_id=run_id, now=NOW, client=source, **kwargs)


def run(setup, runner=None, coverage=None, **kwargs):
    store, path, _ = setup
    return execute_refresh(
        store,
        path,
        run_id="a",
        config_dir=path.parent,
        runner=runner,
        coverage_builder=coverage or (lambda *_: {"mask_union_errors": 0}),
        **kwargs,
    )


def finish_all(store, plan, ledger, request, config):
    ledger.recover_interrupted()
    while item := ledger.claim():
        ledger.finish(item["scene"]["acquisition_key"], {})


def test_exact_selection_deduplicates_ports_and_reuses_frozen_plan(setup):
    store, _, _ = setup
    source = Source([product(22), product(23), product(24)])
    result = plan(setup, source, max_acquisitions=2)
    assert len(source.calls) == 6
    assert result["discovered_acquisitions"] == 3
    assert result["registered_acquisitions"] == 3
    assert result["deferred_acquisitions"] == 1
    assert result["selected_acquisition_keys"] == [
        acquisition_key(product(day)["Name"]) for day in (22, 23)
    ]
    assert result["planned_zone_observations"] == 6
    assert "private-source-token" not in json.dumps(result)
    assert str(store.data_dir) not in json.dumps(result)
    assert plan(setup, source, max_acquisitions=2) == result
    assert len(source.calls) == 6
    with pytest.raises(ValueError, match="frozen request"):
        plan(setup, source, max_acquisitions=3)
    second = plan(setup, source, run_id="b", max_acquisitions=2)
    assert second["selected_acquisition_keys"] == [acquisition_key(product(24)["Name"])]
    assert second["registered_acquisitions"] == 0
    assert second["excluded"] == {"owned_by_existing_campaign": 2}


def test_dry_run_leaves_catalog_campaigns_and_watermarks_untouched(setup):
    store, _, _ = setup
    result = plan(setup, Source([product()]), dry_run=True)
    assert result["status"] == "preview"
    assert result["selected_acquisition_keys"] == [acquisition_key(product()["Name"])]
    assert not (store.data_dir / "history").exists()
    assert not (store.data_dir / "refresh/watermarks.json").exists()
    with store.connect() as tx:
        assert tx.execute("SELECT count(*) FROM acquisitions").fetchone()[0] == 0


def test_partial_search_never_advances_watermark_or_registers_and_resumes_only_missing_queries(
    setup,
):
    store, _, _ = setup
    source = Source([product()], fail_sensor="sentinel-1")
    result = plan(setup, source)
    assert result["status"] == "discovery_incomplete"
    assert not (store.data_dir / "refresh/watermarks.json").exists()
    assert "private-source-token" not in json.dumps(result)
    with store.connect() as tx:
        assert tx.execute("SELECT count(*) FROM acquisitions").fetchone()[0] == 0
    source.fail_sensor = None
    complete = plan(setup, source)
    assert len(source.calls) == 9
    assert complete["status"] == "planned"
    assert all(
        item["last_complete_search_at"] == "2026-10-26T00:00:00.000000Z"
        for item in freshness_report(store)["items"]
    )


def test_crash_after_complete_discovery_reuses_exact_evidence_and_selection(setup, monkeypatch):
    import puertogt.history as history

    store, path, _ = setup
    original = history.create_selected_plan
    monkeypatch.setattr(
        history,
        "create_selected_plan",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("private-source-token")),
    )
    source = Source([product()])
    with pytest.raises(RuntimeError):
        plan(setup, source)
    evidence = store.data_dir / "refresh/runs/a/discovery.json"
    baseline = sha256_file(evidence)
    receipt = json.loads((evidence.parent / "receipt.json").read_text())
    assert receipt["status"] == "planning_failed"
    assert "private-source-token" not in json.dumps(receipt)
    monkeypatch.setattr(history, "create_selected_plan", original)
    result = plan_refresh(store, path, run_id="a", client=source, now=NOW + timedelta(hours=1))
    assert result["status"] == "planned"
    assert result["registered_acquisitions"] == 1
    assert sha256_file(evidence) == baseline
    assert len(source.calls) == 6


def test_exceptions_and_protected_pilots_are_never_scheduled_or_changed(setup):
    store, _, features = setup
    unavailable, pilot = normalize_products(
        [product(21), product(22)], port_ids=["prq", "pbr", "stc"]
    )
    unavailable.metadata["purpose"] = "public_history"
    store.register_scene(unavailable)
    directory = store.data_dir / "history/full-v1"
    old_plan = create_selected_plan(
        store, features, directory, acquisition_keys=[unavailable.acquisition_key]
    )
    ledger = HistoryLedger(directory, HistoryLimits())
    ledger.initialize_plan(old_plan)
    ledger.claim()
    ledger.fail(unavailable.acquisition_key, RuntimeError("source unavailable"), permanent=True)
    JobQueue(store).enqueue(pilot, "prq-zone", "pilot", port_id="prq")
    with store.connect() as tx:
        original = tuple(
            tx.execute(
                "SELECT data_json FROM acquisitions WHERE acquisition_key=?",
                (pilot.acquisition_key,),
            ).fetchone()
        )
        observations = [tuple(row) for row in tx.execute("SELECT * FROM observations")]
    before = sha256_file(directory / "plan.json")
    result = plan(setup, Source([product(21), product(22), product(23)]))
    assert result["selected_acquisition_keys"] == [acquisition_key(product(23)["Name"])]
    assert result["excluded"] == {
        "owned_by_existing_campaign": 1,
        "already_observed_or_protected": 1,
    }
    with store.connect() as tx:
        assert (
            tuple(
                tx.execute(
                    "SELECT data_json FROM acquisitions WHERE acquisition_key=?",
                    (pilot.acquisition_key,),
                ).fetchone()
            )
            == original
        )
        assert [tuple(row) for row in tx.execute("SELECT * FROM observations")] == observations
    assert sha256_file(directory / "plan.json") == before
    assert ledger.summary()["states"] == {"unavailable": 1}


def test_resume_uses_same_ledger_then_completed_repeat_does_no_work(setup):
    store, _, _ = setup
    result = plan(setup, Source([product(22), product(23)]))
    calls = []

    def interrupted(store, plan, ledger, request, config):
        calls.append(1)
        item = ledger.claim()
        ledger.finish(item["scene"]["acquisition_key"], {})
        ledger.claim()
        raise RuntimeError("private-source-token")

    with pytest.raises(RuntimeError):
        run(setup, interrupted)
    receipt = json.loads((store.data_dir / "refresh/runs/a/receipt.json").read_text())
    assert receipt["status"] == "processing_interrupted"
    assert "private-source-token" not in json.dumps(receipt)
    completed = run(setup, finish_all)
    assert completed["processing"]["states"] == {"complete": 2}
    assert completed["plan_id"] == result["plan_id"]
    assert completed["status"] == "complete"
    assert run(setup, interrupted) == completed
    assert len(calls) == 1


def test_coverage_failure_retries_coverage_without_reprocessing(setup):
    plan(setup, Source([product()]))
    calls = []

    def process(*args):
        calls.append(1)
        finish_all(*args)

    def broken(*args):
        raise RuntimeError("private-source-token")

    failed = run(setup, process, broken)
    assert failed["status"] == "coverage_failed"
    assert "finished_at" not in failed
    assert "private-source-token" not in json.dumps(failed)
    assert run(setup, process)["status"] == "complete"
    assert len(calls) == 1


def test_empty_refresh_still_rebuilds_coverage_and_reports_freshness(setup):
    store, _, _ = setup
    result = plan(setup, Source())
    assert result["status"] == "no_new_acquisitions"
    assert result["plan_id"] is None
    calls = []
    completed = run(setup, coverage=lambda *_: calls.append(1) or {"mask_union_errors": 0})
    assert completed["status"] == "complete"
    assert len(calls) == 1
    assert not (store.data_dir / "history").exists()
    assert all(item["last_catalog_capture_at"] is None for item in freshness_report(store)["items"])


@pytest.mark.parametrize("relative", ["request.json", "selection.json", "discovery.json"])
def test_frozen_evidence_changes_fail_before_processing(setup, relative):
    store, _, _ = setup
    plan(setup, Source([product()]))
    path = store.data_dir / "refresh/runs/a" / relative
    value = json.loads(path.read_text())
    value["tamper"] = True
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="changed"):
        run(setup, lambda *_: pytest.fail("must not process changed evidence"))


def test_unreadable_history_fails_closed_and_single_refresh_lock_blocks_duplicate(setup):
    store, _, _ = setup
    with file_lock(store.data_dir / "refresh/refresh.lock"):
        with pytest.raises(RuntimeError):
            plan(setup, Source())
    broken = store.data_dir / "history/broken"
    broken.mkdir(parents=True)
    (broken / "progress.sqlite").write_text("private-source-token")
    with pytest.raises(ValueError, match="unreadable"):
        plan(setup, Source([product()]))
    assert freshness_report(store)["errors"] == ["campaign_state_unavailable"]


def test_freshness_separates_capture_processing_useful_and_pending(setup):
    store, _, _ = setup
    first, last = normalize_products([product(22), product(23)], port_ids=["prq"])
    queue = JobQueue(store)
    queue.enqueue(first, "prq-zone", "demo", port_id="prq")
    queue.enqueue(last, "prq-zone", "demo", port_id="prq")
    with store.transaction() as tx:
        tx.execute(
            "UPDATE observations SET status='usable',observable_fraction=1,processed_at='2026-10-25T00:00:00.000000Z' WHERE acquisition_key=?",
            (first.acquisition_key,),
        )
        tx.execute(
            "UPDATE observations SET status='partial',observable_fraction=.5,processed_at='2026-10-26T00:00:00.000000Z' WHERE acquisition_key=?",
            (last.acquisition_key,),
        )
    item = next(
        row
        for row in freshness_report(store)["items"]
        if row["port_id"] == "prq" and row["sensor"] == "sentinel-2"
    )
    assert item["last_complete_search_at"] is None
    assert item["last_catalog_capture_at"].startswith("2026-10-23")
    assert item["last_processed_capture_at"].startswith("2026-10-23")
    assert item["last_processed_at"].startswith("2026-10-26")
    assert item["last_useful_capture_at"].startswith("2026-10-22")
    assert item["pending_acquisitions"] == 0
    queue.enqueue(last, "another-zone", "demo", port_id="prq")
    item = next(
        row
        for row in freshness_report(store)["items"]
        if row["port_id"] == "prq" and row["sensor"] == "sentinel-2"
    )
    assert item["pending_acquisitions"] == 1


def test_real_coverage_builder_runs_on_fixture_without_pixels_or_models(setup):
    store, path, _ = setup
    plan(setup, Source())
    result = execute_refresh(store, path, run_id="a", config_dir=path.parent)
    assert result["status"] == "complete"
    assert result["coverage"]["mask_union_errors"] == 0
    assert result["coverage"]["mask_union_entries"] == 0
    assert len(result["coverage"]["source_snapshot_sha256"]) == 64
    assert (store.data_dir / "coverage/pass-unions-v1.json").is_file()


def test_processing_diagnostics_stay_private_and_paused_resume_preserves_limits(setup, capsys):
    store, _, _ = setup
    result = plan(setup, Source([product()]))
    directory = store.data_dir / "history/refresh-a"
    ledger = HistoryLedger(directory, HistoryLimits(**result["limits"]))
    with ledger.store.transaction() as tx:
        tx.execute("UPDATE history_acquisitions SET status='paused',attempts=1,bytes_reserved=123")

    def process(*args):
        print("https://host?token=private-source-token")
        finish_all(*args)

    completed = run(setup, process, resume_paused=True)
    assert completed["processing"] == {"states": {"complete": 1}, "download_bytes_reserved": 123}
    assert json.loads((directory / "limits.json").read_text()) == result["limits"]
    assert "private-source-token" not in capsys.readouterr().out
    assert "private-source-token" in (store.data_dir / "refresh/runs/a/processing.log").read_text()


def test_incomplete_new_run_keeps_previous_complete_search_watermark(setup):
    store, path, _ = setup
    plan(setup, Source())
    original = (store.data_dir / "refresh/watermarks.json").read_bytes()
    result = plan_refresh(
        store,
        path,
        run_id="b",
        client=Source(fail_sensor="sentinel-2"),
        now=NOW + timedelta(days=1),
    )
    assert result["status"] == "discovery_incomplete"
    assert (store.data_dir / "refresh/watermarks.json").read_bytes() == original


def test_incomplete_pagination_is_not_complete_discovery(setup):
    class Partial(Source):
        def search(self, *args, **kwargs):
            return CatalogSearch([], [], 1)

    assert plan(setup, Partial())["status"] == "discovery_incomplete"


def test_refresh_status_is_in_system_snapshot_with_null_initial_watermarks(setup):
    from puertogt.status import system_status

    store, _, _ = setup
    report = system_status(store)
    assert len(report["freshness"]["items"]) == 6
    assert all(item["last_complete_search_at"] is None for item in report["freshness"]["items"])


def test_ledger_outside_frozen_selection_never_reaches_processor(setup):
    store, _, _ = setup
    receipt = plan(setup, Source([product()]))
    ledger = HistoryLedger(store.data_dir / "history/refresh-a", HistoryLimits(**receipt["limits"]))
    with ledger.store.transaction() as tx:
        tx.execute("UPDATE history_acquisitions SET plan_json='{}'")
    with pytest.raises(ValueError, match="exact frozen selection"):
        run(setup, lambda *_: pytest.fail("must not process altered ledger inputs"))


def test_coverage_respects_existing_shared_heavy_lock(setup):
    store, _, _ = setup
    plan(setup, Source())
    with file_lock(store.data_dir / ".heavy-worker.lock"):
        result = run(
            setup, coverage=lambda *_: pytest.fail("must not overlap another heavy worker")
        )
    assert result["status"] == "coverage_failed"
    assert result["error_code"] == "ResourceLimit"
    assert run(setup)["status"] == "complete"


def test_cli_errors_and_status_are_sanitized_without_network(setup, capsys):
    import runpy

    store, path, _ = setup
    main = runpy.run_path("scripts/refresh_catalog.py")["main"]
    assert main(["status", "--data-dir", str(store.data_dir)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert len(status["freshness"]["items"]) == 6
    assert (
        main(
            [
                "run",
                "--data-dir",
                str(store.data_dir),
                "--config-dir",
                str(path.parent),
                "--run-id",
                "missing",
            ]
        )
        == 2
    )
    error = capsys.readouterr().out
    assert json.loads(error) == {"status": "error", "error_code": "FileNotFoundError"}
    assert str(store.data_dir) not in error


@pytest.mark.parametrize("missing", ["request.json", "discovery.json", "receipt.json", "all"])
def test_freshness_requires_the_referenced_complete_search_evidence(setup, missing):
    store, _, _ = setup
    plan(setup, Source())
    root = store.data_dir / "refresh/runs/a"
    if missing == "all":
        shutil.rmtree(root)
    else:
        (root / missing).unlink()
    report = freshness_report(store)
    assert all(item["last_complete_search_at"] is None for item in report["items"])
    assert "freshness_search_evidence_unavailable" in report["errors"]
    assert "private-source-token" not in json.dumps(report)


@pytest.mark.parametrize(
    "mutation",
    [
        "dry_run",
        "partial_query",
        "missing_query",
        "query_date",
        "receipt_ports",
        "watermark_date",
        "checksum",
    ],
)
def test_freshness_rejects_inconsistent_search_provenance(setup, mutation):
    from puertogt.refresh import _digest

    store, _, _ = setup
    plan(setup, Source())
    root = store.data_dir / "refresh/runs/a"
    request = json.loads((root / "request.json").read_text())
    discovery = json.loads((root / "discovery.json").read_text())
    receipt = json.loads((root / "receipt.json").read_text())
    if mutation == "dry_run":
        request["dry_run"] = receipt["dry_run"] = True
    elif mutation == "partial_query":
        discovery["queries"][0]["complete"] = False
    elif mutation == "missing_query":
        discovery["queries"].pop()
    elif mutation == "query_date":
        discovery["queries"][0]["capture_start"] = "2020-01-01T00:00:00Z"
    elif mutation == "receipt_ports":
        receipt["port_ids"] = ["prq"]
    elif mutation == "watermark_date":
        path = store.data_dir / "refresh/watermarks.json"
        watermark = json.loads(path.read_text())
        for item in watermark["items"]:
            item["last_complete_search_at"] = "2027-01-01T00:00:00Z"
        path.write_text(json.dumps(watermark))
    elif mutation == "checksum":
        discovery["queries"][0]["page_evidence"].append({"url": "private-source-token"})
    # Recompute outer links, so semantic provenance checks also have to hold.
    discovery["request_sha256"] = receipt["request_sha256"] = _digest(request)
    (root / "request.json").write_text(json.dumps(request))
    (root / "discovery.json").write_text(json.dumps(discovery))
    receipt["discovery_sha256"] = sha256_file(root / "discovery.json")
    (root / "receipt.json").write_text(json.dumps(receipt))
    report = freshness_report(store)
    assert all(item["last_complete_search_at"] is None for item in report["items"])
    assert "freshness_search_evidence_unavailable" in report["errors"]


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_resume_own_ledger_interrupted_after_base_store_initialization(setup, monkeypatch, failure):
    store, _, _ = setup
    original = HistoryLedger.__init__

    def interrupted(self, directory, limits):
        Store(directory / "progress.sqlite", directory)
        raise failure("after base store")

    monkeypatch.setattr(HistoryLedger, "__init__", interrupted)
    source = Source([product()])
    with pytest.raises(failure):
        plan(setup, source)
    directory = store.data_dir / "history/refresh-a"
    plan_hash = sha256_file(directory / "plan.json")
    selection_hash = sha256_file(store.data_dir / "refresh/runs/a/selection.json")
    monkeypatch.setattr(HistoryLedger, "__init__", original)
    resumed = plan(setup, source)
    assert resumed["status"] == "planned"
    assert len(source.calls) == 6
    assert sha256_file(directory / "plan.json") == plan_hash
    assert sha256_file(store.data_dir / "refresh/runs/a/selection.json") == selection_hash
    assert HistoryLedger(directory, HistoryLimits(**resumed["limits"])).summary()["states"] == {
        "pending": 1
    }


@pytest.mark.parametrize(
    "campaign_state,pending,unavailable", [("complete", 1, 0), ("unavailable", 0, 1)]
)
def test_freshness_observation_pending_takes_priority_over_completed_campaign(
    setup, campaign_state, pending, unavailable
):
    store, _, _ = setup
    result = plan(setup, Source([product()]))
    directory = store.data_dir / "history/refresh-a"
    ledger = HistoryLedger(directory, HistoryLimits(**result["limits"]))
    with ledger.store.transaction() as tx:
        tx.execute("UPDATE history_acquisitions SET status=?", (campaign_state,))
    scene = normalize_products([product()], port_ids=["prq", "pbr", "stc"])[0]
    queue = JobQueue(store)
    queue.enqueue(scene, "prq-zone", "fixture", port_id="prq")
    queue.enqueue(scene, "other-zone", "fixture", port_id="prq")
    with store.transaction() as tx:
        tx.execute(
            "UPDATE observations SET status='usable',observable_fraction=1,processed_at='2026-10-26T00:00:00Z' WHERE zone_id='prq-zone'"
        )
    item = next(
        row
        for row in freshness_report(store)["items"]
        if row["port_id"] == "prq" and row["sensor"] == "sentinel-2"
    )
    assert item["pending_acquisitions"] == pending
    assert item["unavailable_acquisitions"] == unavailable


def test_freshness_validates_each_run_once_for_its_six_scopes(setup, monkeypatch):
    from pathlib import Path

    store, _, _ = setup
    plan(setup, Source())
    root = store.data_dir / "refresh/runs/a"
    original, reads = Path.read_bytes, []

    def record(path):
        if path.parent == root:
            reads.append(path.name)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", record)
    report = freshness_report(store)
    assert len(report["items"]) == 6
    assert all(item["last_complete_search_at"] is not None for item in report["items"])
    assert sorted(reads) == ["discovery.json", "receipt.json", "request.json"]


@pytest.mark.parametrize(
    "damage",
    [
        "other_ledger",
        "foreign_rows",
        "extra_table",
        "corrupt_bytes",
        "plan",
        "selection",
        "missing_receipt",
    ],
)
def test_bootstrap_recovery_rejects_unproved_or_nonempty_ledgers(setup, monkeypatch, damage):
    store, _, _ = setup
    directory = store.data_dir / "history/refresh-a"

    def interrupted(self, directory, limits):
        Store(directory / "progress.sqlite", directory)
        raise RuntimeError("after base store")

    source = Source([product()])
    with monkeypatch.context() as patch:
        patch.setattr(HistoryLedger, "__init__", interrupted)
        with pytest.raises(RuntimeError):
            plan(setup, source)
    root = store.data_dir / "refresh/runs/a"
    if damage == "other_ledger":
        other = store.data_dir / "history/other/progress.sqlite"
        Store(other, other.parent)
        original_other = sha256_file(other)
    elif damage in {"foreign_rows", "extra_table"}:
        with sqlite3.connect(directory / "progress.sqlite") as tx:
            if damage == "foreign_rows":
                tx.execute(
                    "INSERT INTO quotas(quota_key,period,limit_units) VALUES('foreign','fixed',1)"
                )
            else:
                tx.execute("CREATE TABLE unknown_campaign_data(secret TEXT)")
    elif damage == "corrupt_bytes":
        (directory / "progress.sqlite").write_bytes(b"private-source-token")
    elif damage == "plan":
        path = directory / "plan.json"
        value = json.loads(path.read_text())
        value["selected_acquisition_keys"] = ["unrelated"]
        path.write_text(json.dumps(value))
    elif damage == "selection":
        path = root / "selection.json"
        value = json.loads(path.read_text())
        value["acquisition_keys"] = ["unrelated"]
        path.write_text(json.dumps(value))
    else:
        (root / "receipt.json").unlink()
    with pytest.raises((ValueError, sqlite3.DatabaseError, FileNotFoundError)):
        plan(setup, source)
    assert len(source.calls) == 6
    if damage == "other_ledger":
        assert sha256_file(other) == original_other
    elif damage != "corrupt_bytes":
        with sqlite3.connect(directory / "progress.sqlite") as tx:
            assert not tx.execute(
                "SELECT 1 FROM sqlite_master WHERE name='history_acquisitions'"
            ).fetchone()
