"""Bounded catalogue refreshes with frozen selections and explicit execution.

Metadata discovery is reusable and complete before registration. Existing
campaigns own their acquisitions forever, including unavailable inputs. A
refresh resumes its own plan; it never silently reschedules those acquisitions.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing, redirect_stderr, redirect_stdout
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shapely.geometry import mapping, shape

from puertogt.catalog import CatalogClient, acquisition_key, load_ports, normalize_products
from puertogt.contracts import Scene, as_utc
from puertogt.store import Store, confined_path, json_text, sha256_file, utc_text

SENSORS = ("sentinel-1", "sentinel-2")
SCHEMA = "catalog-refresh-v1"
QUERY_PUBLIC_FIELDS = {
    "port_id",
    "sensor",
    "complete",
    "capture_start",
    "capture_end_exclusive",
    "products",
    "reported_count",
    "pages",
    "page_evidence_sha256",
    "completed_at",
    "error_code",
}


def _write(path: Path, value: dict) -> None:
    from puertogt.history import _atomic_json

    _atomic_json(path, value)


def _digest(value) -> str:
    return hashlib.sha256(json_text(value).encode()).hexdigest()


def _run_path(store, run_id, *, preview=False):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", run_id):
        raise ValueError("run-id must contain 1..64 letters, digits, underscores or hyphens")
    return confined_path(
        store.data_dir,
        Path("refresh") / ("previews" if preview else "runs") / run_id,
        must_exist=False,
    )


def campaign_index(data_dir: Path, *, strict=False):
    """Read ownership without initializing or mutating historical ledgers."""
    index, errors = defaultdict(list), []
    for path in sorted((data_dir / "history").glob("*/progress.sqlite")):
        try:
            path = confined_path(data_dir, path.relative_to(data_dir))
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.2)) as tx:
                tx.execute("PRAGMA query_only=ON")
                for key, status in tx.execute(
                    "SELECT acquisition_key,status FROM history_acquisitions"
                ):
                    index[key].append({"campaign": path.parent.name, "status": status})
        except (OSError, sqlite3.Error, ValueError):
            errors.append("campaign_state_unavailable")
    if strict and errors:
        raise ValueError(
            "Historical ownership is unreadable; refusing a potentially duplicate plan"
        )
    return index, errors


def _catalog_snapshot(store):
    with closing(store.connect()) as tx:
        tx.execute("BEGIN")
        scenes = {
            row["acquisition_key"]: Scene.model_validate_json(row["data_json"])
            for row in tx.execute("SELECT acquisition_key,data_json FROM acquisitions")
        }
        observed = {
            row[0] for row in tx.execute("SELECT DISTINCT acquisition_key FROM observations")
        }
    return scenes, observed


def _discover(request, ports, path, client, now, clock=None):
    clock = clock or (lambda: datetime.now(timezone.utc))
    report = (
        json.loads(path.read_text())
        if path.exists()
        else {
            "schema": SCHEMA,
            "request_sha256": _digest(request),
            "queries": [],
            "products": [],
        }
    )
    if report["request_sha256"] != _digest(request):
        raise ValueError("Discovery receipt differs from the frozen request")
    if report.get("status") == "complete":
        return report
    products = {product["Id"]: product for product in report["products"]}
    queries = {(query["port_id"], query["sensor"]): query for query in report["queries"]}
    start = datetime.fromisoformat(request["capture_start"])
    end = datetime.fromisoformat(request["capture_end_exclusive"])
    cutoff = datetime.fromisoformat(request["publication_cutoff"])
    for port_id in request["port_ids"]:
        geometry = shape(ports[port_id]["geometry"])
        for sensor in request["sensors"]:
            previous = queries.get((port_id, sensor))
            if previous and previous["complete"]:
                continue
            query = {
                "port_id": port_id,
                "sensor": sensor,
                "complete": False,
                "capture_start": request["capture_start"],
                "capture_end_exclusive": request["capture_end_exclusive"],
            }
            try:
                result = client.search(sensor, mapping(geometry.envelope), start, end, as_of=cutoff)
                if not result.complete:
                    raise ValueError("Incomplete provider pagination")
                # Exact local geometry follows the provider's bounded envelope.
                matching = [
                    product
                    for product in result.products
                    if shape(product["GeoFootprint"]).intersection(geometry).area > 0
                ]
                normalized = normalize_products(matching, discovered_at=now)
                if any(
                    scene.sensor != sensor
                    or not start <= scene.captured_at < end
                    or (scene.published_at and scene.published_at > cutoff)
                    for scene in normalized
                ):
                    raise ValueError(
                        "Provider result differs from requested sensor or time interval"
                    )
                products.update({product["Id"]: product for product in matching})
                query.update(
                    complete=True,
                    products=len(result.products),
                    reported_count=result.reported_count,
                    pages=len(result.pages),
                    product_ids=sorted(product["Id"] for product in matching),
                    acquisition_keys=sorted({acquisition_key(p["Name"]) for p in matching}),
                    page_evidence=result.pages,
                    page_evidence_sha256=_digest(result.pages),
                    completed_at=utc_text(clock()),
                )
            except Exception as exc:
                # Never persist arbitrary exception text or URLs into operator/public receipts.
                query["error_code"] = type(exc).__name__
            queries[port_id, sensor] = query
            report.update(
                queries=list(queries.values()),
                products=list(products.values()),
                status="incomplete",
            )
            _write(path, report)
    complete = len(queries) == len(request["port_ids"]) * len(request["sensors"]) and all(
        query["complete"] for query in queries.values()
    )
    report.update(status="complete" if complete else "incomplete", completed_at=utc_text(clock()))
    if complete:
        membership = defaultdict(set)
        for query in queries.values():
            for key in query["acquisition_keys"]:
                membership[key].add(query["port_id"])
        scenes = normalize_products(list(products.values()), discovered_at=now)
        report["scenes"] = [
            scene.model_copy(
                update={
                    "port_ids": sorted(membership[scene.acquisition_key]),
                    "metadata": {
                        **scene.metadata,
                        "purpose": "public_history",
                        "refresh_origin_run": request["run_id"],
                    },
                }
            ).model_dump(mode="json")
            for scene in scenes
        ]
    _write(path, report)
    return report


def _watermark(store, request, discovery):
    path = store.data_dir / "refresh/watermarks.json"
    existing = json.loads(path.read_text()) if path.exists() else {"schema": SCHEMA, "items": []}
    items = {(item["port_id"], item["sensor"]): item for item in existing["items"]}
    for query in discovery["queries"]:
        key = query["port_id"], query["sensor"]
        value = {
            "port_id": key[0],
            "sensor": key[1],
            "run_id": request["run_id"],
            "last_complete_search_at": discovery["completed_at"],
            "searched_capture_start": request["capture_start"],
            "searched_capture_end_exclusive": request["capture_end_exclusive"],
        }
        if (
            key not in items
            or items[key]["last_complete_search_at"] <= value["last_complete_search_at"]
        ):
            items[key] = value
    _write(path, {"schema": SCHEMA, "items": list(items.values())})


def plan_refresh(
    store: Store,
    ports_path: Path,
    *,
    run_id: str,
    lookback_days=90,
    end: datetime | None = None,
    max_acquisitions=5,
    limits=None,
    threads=4,
    port_ids=None,
    sensors=None,
    client=None,
    dry_run=False,
    now=None,
) -> dict:
    """Discover, register unseen metadata and freeze a bounded exact selection.

    Dry runs write isolated preview receipts but do not register metadata, advance
    watermarks, create campaigns or download pixels. Repeating a run ID reuses its
    request, discovery evidence and selection, including after interrupted setup.
    """
    from puertogt.history import HistoryLimits, file_lock

    if not 1 <= lookback_days <= 730 or not 1 <= max_acquisitions <= 100 or not 1 <= threads <= 4:
        raise ValueError("Require lookback1..730 days, acquisitions1..100, CPU threads1..4")
    pinned_now = now is not None
    now = as_utc(now or datetime.now(timezone.utc))
    ports = load_ports(ports_path)
    selected_ports = sorted(set(ports if port_ids is None else port_ids))
    selected_sensors = sorted(set(SENSORS if sensors is None else sensors))
    if (
        not selected_ports
        or not selected_sensors
        or not set(selected_ports).issubset(ports)
        or not set(selected_sensors).issubset(SENSORS)
    ):
        raise ValueError("Unknown port or sensor")
    limits = limits or HistoryLimits(
        total_download_bytes=2 * 1024**3, acquisition_download_bytes=512 * 1024**2, io_workers=4
    )
    root = _run_path(store, run_id, preview=dry_run)
    lock = store.data_dir / "refresh/refresh.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(lock):
        saved = (
            json.loads((root / "request.json").read_text())
            if (root / "request.json").exists()
            else None
        )
        cutoff = (
            as_utc(end)
            if end
            else (datetime.fromisoformat(saved["capture_end_exclusive"]) if saved else now)
        )
        if cutoff > now:
            raise ValueError("A refresh cannot search future captures")
        request = {
            "schema": SCHEMA,
            "run_id": run_id,
            "capture_start": utc_text(cutoff - timedelta(days=lookback_days)),
            "capture_end_exclusive": utc_text(cutoff),
            "publication_cutoff": saved["publication_cutoff"] if saved else utc_text(now),
            "ports_sha256": sha256_file(ports_path),
            "port_ids": selected_ports,
            "sensors": selected_sensors,
            "model_configs_sha256": {
                name: sha256_file(Path(ports_path).parent / "models" / name)
                for name in ("s1.yaml", "s2.yaml")
                if (Path(ports_path).parent / "models" / name).is_file()
            },
            "max_acquisitions": max_acquisitions,
            "limits": asdict(limits),
            "threads": threads,
            "dry_run": dry_run,
        }
        if saved is not None and saved != request:
            raise ValueError("Run ID already has a different frozen request; use a new run ID")
        if saved is None:
            _write(root / "request.json", request)
        receipt_path = root / "receipt.json"
        if receipt_path.exists():
            prior = json.loads(receipt_path.read_text())
            if prior["status"] not in {
                "discovery_incomplete",
                "planning_failed",
                "initializing_ledger",
            }:
                return prior
        discovery = _discover(
            request,
            ports,
            root / "discovery.json",
            client or CatalogClient(),
            now,
            clock=(lambda: now) if pinned_now else None,
        )
        receipt = {
            "schema": SCHEMA,
            "run_id": run_id,
            "dry_run": dry_run,
            "request_sha256": _digest(request),
            "discovery_sha256": sha256_file(root / "discovery.json"),
            "capture_start": request["capture_start"],
            "capture_end_exclusive": request["capture_end_exclusive"],
            "port_ids": selected_ports,
            "sensors": selected_sensors,
            "limits": request["limits"],
            "threads": threads,
            "max_acquisitions": max_acquisitions,
            "queries": [
                {k: v for k, v in q.items() if k in QUERY_PUBLIC_FIELDS}
                for q in discovery["queries"]
            ],
            "created_at": utc_text(now),
        }
        if discovery["status"] != "complete":
            receipt["status"] = "discovery_incomplete"
            _write(receipt_path, receipt)
            return receipt
        try:
            return _freeze_selection(store, ports_path, root, request, receipt, discovery, limits)
        except Exception as exc:
            # Retain the durable ownership proof if failure occurs before setup
            # has reconstructed its in-memory selection (for example a lock).
            if receipt_path.exists():
                previous = json.loads(receipt_path.read_text())
                if (
                    previous.get("request_sha256") == receipt["request_sha256"]
                    and previous.get("discovery_sha256") == receipt["discovery_sha256"]
                ):
                    receipt = {**previous, **receipt}
            receipt.update(status="planning_failed", error_code=type(exc).__name__)
            _write(receipt_path, receipt)
            raise


def _freeze_selection(store, ports_path, root, request, receipt, discovery, limits):
    from puertogt.history import HistoryLedger, create_selected_plan

    dry_run, run_id = request["dry_run"], request["run_id"]
    max_acquisitions = request["max_acquisitions"]
    receipt_path = root / "receipt.json"
    directory = confined_path(
        store.data_dir, Path("history") / ("refresh-" + run_id), must_exist=False
    )
    if not dry_run:
        _recover_initial_ledger(store, ports_path, root, directory, request, receipt, limits)
    known, _ = campaign_index(store.data_dir, strict=True)
    existing, observed = _catalog_snapshot(store)
    scenes = [Scene.model_validate(value) for value in discovery["scenes"]]
    excluded = defaultdict(int)
    eligible = []
    for scene in scenes:
        key = scene.acquisition_key
        old = existing.get(key)
        if key in known:
            excluded["owned_by_existing_campaign"] += 1
        elif key in observed:
            excluded["already_observed_or_protected"] += 1
        elif old and (
            old.metadata.get("context_only")
            or old.metadata.get("purpose") not in {None, "public_history"}
        ):
            excluded["context_only"] += 1
        else:
            eligible.append(scene)
    eligible.sort(key=lambda scene: (scene.captured_at, scene.acquisition_key))
    selection_path = root / "selection.json"
    if selection_path.exists():
        selection = json.loads(selection_path.read_text())
        if selection["discovery_sha256"] != receipt["discovery_sha256"]:
            raise ValueError("Frozen selection discovery changed")
    else:
        if directory.exists():
            raise ValueError("Campaign directory already exists without this refresh selection")
        selection = {
            "acquisition_keys": [s.acquisition_key for s in eligible[:max_acquisitions]],
            "discovery_sha256": receipt["discovery_sha256"],
            "eligible_acquisitions": len(eligible),
            "excluded": dict(excluded),
            "new_catalog_keys": sorted(
                s.acquisition_key for s in scenes if s.acquisition_key not in existing
            ),
        }
        _write(selection_path, selection)
    selected_keys = selection["acquisition_keys"]
    if (
        len(selected_keys) > max_acquisitions
        or len(selected_keys) != len(set(selected_keys))
        or not set(selected_keys).issubset(s.acquisition_key for s in scenes)
    ):
        raise ValueError("Invalid frozen selection")
    for key in selected_keys:
        if any(entry["campaign"] != directory.name for entry in known.get(key, [])):
            raise ValueError("Selected acquisition belongs to another campaign")
    receipt.update(
        selected_acquisition_keys=selection["acquisition_keys"],
        discovered_acquisitions=len(scenes),
        eligible_acquisitions=selection["eligible_acquisitions"],
        deferred_acquisitions=selection["eligible_acquisitions"] - len(selected_keys),
        excluded=selection["excluded"],
        selection_sha256=sha256_file(selection_path),
        registered_acquisitions=0,
        plan_id=None,
        plan_sha256=None,
        status="preview" if dry_run else "planned",
    )
    if not dry_run:
        # Preserve all existing acquisition rows, especially frozen pilots.
        # Legacy metadata-only update rows may need the purpose marker, but
        # only unobserved/unclaimed captures from this discovery qualify.
        eligible_keys = {scene.acquisition_key for scene in eligible}
        with store.transaction() as tx:
            for scene in scenes:
                key = scene.acquisition_key
                prior = tx.execute(
                    "SELECT metadata_json,data_json FROM acquisitions WHERE acquisition_key=?",
                    (key,),
                ).fetchone()
                if prior is None:
                    store.register_scene(scene, connection=tx)
                elif (
                    key not in known
                    and key in eligible_keys
                    and not tx.execute(
                        "SELECT 1 FROM observations WHERE acquisition_key=? LIMIT 1", (key,)
                    ).fetchone()
                ):
                    metadata, data = json.loads(prior[0]), json.loads(prior[1])
                    if not metadata.get("purpose"):
                        metadata["purpose"] = "public_history"
                        data["metadata"] = metadata
                        tx.execute(
                            "UPDATE acquisitions SET metadata_json=?,data_json=? WHERE acquisition_key=?",
                            (json_text(metadata), json_text(data), key),
                        )
        receipt["registered_acquisitions"] = len(selection["new_catalog_keys"])
        _watermark(store, request, discovery)
        if selection["acquisition_keys"]:
            features = json.loads(Path(ports_path).read_text())["features"]
            plan = create_selected_plan(
                store, features, directory, acquisition_keys=selection["acquisition_keys"]
            )
            receipt.update(
                plan_id=plan["plan_id"],
                plan_sha256=sha256_file(directory / "plan.json"),
                planned_zone_observations=sum(len(item["zones"]) for item in plan["acquisitions"]),
                status="initializing_ledger",
            )
            # Durable ownership evidence precedes constructor writes. A hard
            # interruption between Store and the history tables is resumable.
            _write(receipt_path, receipt)
            HistoryLedger(directory, limits).initialize_plan(plan)
            receipt["status"] = "planned"
        else:
            receipt["status"] = "no_new_acquisitions"
    _write(receipt_path, receipt)
    return receipt


def _recover_initial_ledger(store, ports_path, root, directory, request, receipt, limits):
    """Finish only a proved, empty Store bootstrap belonging to this exact run."""
    from puertogt.history import HistoryLedger, create_selected_plan, file_lock
    from puertogt.store import SCHEMA as STORE_SCHEMA
    from puertogt.store import SCHEMA_VERSION

    path = directory / "progress.sqlite"
    if not path.exists():
        return
    path = confined_path(store.data_dir, path.relative_to(store.data_dir))
    with file_lock(directory / "campaign.lock"):
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as tx:
            tx.execute("BEGIN")
            if tx.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='history_acquisitions'"
            ).fetchone():
                return
            # A damaged or nonempty database is never a recoverable bootstrap.
            with closing(sqlite3.connect(":memory:")) as expected:
                expected.executescript(STORE_SCHEMA)
                schema_query = "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
                if tx.execute(schema_query).fetchall() != expected.execute(schema_query).fetchall():
                    raise ValueError("Incomplete ledger differs from the empty base schema")
                tables = [
                    row[0]
                    for row in expected.execute("SELECT name FROM sqlite_master WHERE type='table'")
                ]
            if tx.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("Incomplete ledger failed integrity checks")
            if tx.execute("SELECT version FROM schema_migrations").fetchall() != [
                (SCHEMA_VERSION,)
            ]:
                raise ValueError("Incomplete ledger has unexpected migrations")
            for table in tables:
                if (
                    table != "schema_migrations"
                    and tx.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone()
                ):
                    raise ValueError("Incomplete ledger contains unrelated data")
        selection_path = confined_path(
            store.data_dir, (root / "selection.json").relative_to(store.data_dir)
        )
        prior_path = confined_path(
            store.data_dir, (root / "receipt.json").relative_to(store.data_dir)
        )
        plan_path = confined_path(
            store.data_dir, (directory / "plan.json").relative_to(store.data_dir)
        )
        selection, prior = (
            json.loads(selection_path.read_text()),
            json.loads(prior_path.read_text()),
        )
        if (
            prior.get("status") not in {"initializing_ledger", "planning_failed"}
            or prior.get("run_id") != request["run_id"]
            or prior.get("dry_run") is not False
            or prior.get("request_sha256") != _digest(request)
            or prior.get("discovery_sha256") != receipt["discovery_sha256"]
            or selection.get("discovery_sha256") != receipt["discovery_sha256"]
            or prior.get("selection_sha256") != sha256_file(selection_path)
            or prior.get("selected_acquisition_keys") != selection.get("acquisition_keys")
        ):
            raise ValueError("Incomplete ledger lacks matching frozen ownership evidence")
        if prior.get("plan_sha256") and prior["plan_sha256"] != sha256_file(plan_path):
            raise ValueError("Incomplete ledger processing plan changed")
        if (directory / "limits.json").exists() and json.loads(
            (directory / "limits.json").read_text()
        ) != request["limits"]:
            raise ValueError("Incomplete ledger resource limits changed")
        features = json.loads(Path(ports_path).read_text())["features"]
        # Existing-plan loading validates its identity, exact keys and geometries.
        plan = create_selected_plan(
            store, features, directory, acquisition_keys=selection["acquisition_keys"]
        )
        if prior.get("plan_id") and prior["plan_id"] != plan["plan_id"]:
            raise ValueError("Incomplete ledger processing identity changed")
        HistoryLedger(directory, limits).initialize_plan(plan)


def _run_history(store, plan, ledger, request, config_dir):
    for name in ("s1.yaml", "s2.yaml"):
        if request["model_configs_sha256"].get(name) != sha256_file(config_dir / "models" / name):
            raise ValueError("Model configuration differs from the frozen refresh request")

    import torch

    from puertogt.cli import read_model
    from puertogt.history import run_history
    from puertogt.ml.public_attribute_transport import RangeSession

    torch.set_num_threads(request["threads"])
    models = {
        sensor: read_model(config_dir / "models" / name, store.data_dir)
        for sensor, name in [("sentinel-1", "s1.yaml"), ("sentinel-2", "s2.yaml")]
    }
    with RangeSession(max_connections=ledger.limits.io_workers) as transport:
        return run_history(
            store,
            plan,
            ledger,
            models,
            max_acquisitions=request["max_acquisitions"],
            quality_models=store.data_dir / "models/omnicloudmask",
            range_opener=transport.open,
        )


def _rebuild_coverage(store, ports_path, output):
    from puertogt.pass_coverage import pass_coverage_report
    from puertogt.pass_masks import build_pass_union_cache

    cache = store.data_dir / "coverage/pass-unions-v1.json"
    masks = build_pass_union_cache(store, ports_path, cache)
    report = pass_coverage_report(store, ports_path, mask_union_cache=cache)
    _write(output, report)
    return {
        "report_sha256": sha256_file(output),
        "mask_cache_sha256": sha256_file(cache),
        "mask_union_entries": len(masks["entries"]),
        "mask_union_errors": len(masks["errors"]),
        "source_snapshot_sha256": report["source"]["snapshot_sha256"],
        "rebuilt_at": report["generated_at"],
    }


def execute_refresh(
    store: Store,
    ports_path: Path,
    *,
    run_id: str,
    config_dir: Path,
    runner=None,
    coverage_builder=None,
    resume_paused=False,
) -> dict:
    """Execute/resume only a previously reviewed plan, with its frozen limits."""
    from puertogt.history import HistoryLedger, HistoryLimits, file_lock

    root = _run_path(store, run_id)
    with file_lock(store.data_dir / "refresh/refresh.lock"):
        request = json.loads((root / "request.json").read_text())
        receipt = json.loads((root / "receipt.json").read_text())
        if (
            receipt["request_sha256"] != _digest(request)
            or sha256_file(ports_path) != request["ports_sha256"]
        ):
            raise ValueError("Frozen request or zone configuration changed")
        if sha256_file(root / "discovery.json") != receipt["discovery_sha256"]:
            raise ValueError("Discovery evidence changed")
        if receipt["status"] in {"preview", "discovery_incomplete", "planning_failed"}:
            raise ValueError("Only a complete planned refresh can execute")
        if sha256_file(root / "selection.json") != receipt["selection_sha256"]:
            raise ValueError("Frozen selection changed")
        if receipt["plan_id"]:
            directory = confined_path(
                store.data_dir, Path("history") / ("refresh-" + run_id), must_exist=False
            )
            if sha256_file(directory / "plan.json") != receipt["plan_sha256"]:
                raise ValueError("Frozen processing plan changed")
            plan = json.loads((directory / "plan.json").read_text())
            limits = HistoryLimits(**request["limits"])
            if json.loads((directory / "limits.json").read_text()) != request["limits"]:
                raise ValueError("Frozen resource limits changed")
            ledger_path = confined_path(
                store.data_dir, (directory / "progress.sqlite").relative_to(store.data_dir)
            )
            with closing(sqlite3.connect(ledger_path.as_uri() + "?mode=ro", uri=True)) as tx:
                actual = {
                    key: json.loads(item)
                    for key, item in tx.execute(
                        "SELECT acquisition_key,plan_json FROM history_acquisitions"
                    )
                }
            expected = {item["scene"]["acquisition_key"]: item for item in plan["acquisitions"]}
            if actual != expected:
                raise ValueError("Processing ledger differs from the exact frozen selection")
            if receipt["status"] == "complete":
                return receipt
            ledger = HistoryLedger(directory, limits)
            if resume_paused:
                # Explicit recovery preserves budgets, attempts and frozen input.
                # Acquiring the campaign lock prevents changing a live worker.
                with file_lock(directory / "campaign.lock"):
                    with ledger.store.transaction() as tx:
                        tx.execute(
                            "UPDATE history_acquisitions SET status='retry',not_before=? WHERE status='paused'",
                            (utc_text(),),
                        )
            if any(
                state not in {"complete", "unavailable"} for state in ledger.summary()["states"]
            ):
                receipt["status"] = "processing"
                _write(root / "receipt.json", receipt)
                try:
                    # The existing processor prints source diagnostics. Keep those
                    # private; only the sanitized receipt reaches public stdout.
                    with (
                        (root / "processing.log").open("a") as log,
                        redirect_stdout(log),
                        redirect_stderr(log),
                    ):
                        (runner or _run_history)(store, plan, ledger, request, Path(config_dir))
                except Exception as exc:
                    import traceback

                    with (root / "processing.log").open("a") as log:
                        traceback.print_exc(file=log)
                    receipt.update(status="processing_interrupted", error_code=type(exc).__name__)
                    _write(root / "receipt.json", receipt)
                    raise
            summary = ledger.summary()
            receipt["processing"] = {
                "states": summary["states"],
                "download_bytes_reserved": summary["download_bytes_reserved"],
            }
            pending = sum(
                count
                for state, count in summary["states"].items()
                if state not in {"complete", "unavailable"}
            )
        else:
            if receipt["status"] == "complete":
                return receipt
            pending = 0
            receipt["processing"] = {"states": {}, "download_bytes_reserved": 0}
        try:
            with file_lock(store.data_dir / ".heavy-worker.lock"):
                receipt["coverage"] = (coverage_builder or _rebuild_coverage)(
                    store, ports_path, root / "coverage.json"
                )
            receipt["status"] = (
                "pending"
                if pending
                else (
                    "coverage_incomplete"
                    if receipt["coverage"].get("mask_union_errors")
                    else "complete"
                )
            )
            receipt.pop("error_code", None)
        except Exception as exc:
            receipt.update(status="coverage_failed", error_code=type(exc).__name__)
        receipt["updated_at"] = utc_text()
        if receipt["status"] == "complete":
            receipt["finished_at"] = receipt["updated_at"]
        _write(root / "receipt.json", receipt)
        return receipt


def _verified_complete_search(store, run_id):
    """Validate referenced immutable evidence once per run and status snapshot."""
    root = _run_path(store, run_id)
    documents, raw = {}, {}
    for name in ("request", "discovery", "receipt"):
        path = confined_path(store.data_dir, (root / f"{name}.json").relative_to(store.data_dir))
        raw[name] = path.read_bytes()
        documents[name] = json.loads(raw[name])
    request, discovery, receipt = (documents[name] for name in ("request", "discovery", "receipt"))
    if (
        any(value.get("schema") != SCHEMA for value in documents.values())
        or request["run_id"] != run_id
        or receipt["run_id"] != run_id
        or request["dry_run"] is not False
        or receipt["dry_run"] is not False
        or receipt["request_sha256"] != _digest(request)
        or discovery["request_sha256"] != _digest(request)
        or receipt["discovery_sha256"] != hashlib.sha256(raw["discovery"]).hexdigest()
        or discovery["status"] != "complete"
        or receipt["status"] in {"preview", "discovery_incomplete"}
    ):
        raise ValueError("Complete search evidence identity differs")
    for field in ("port_ids", "sensors", "capture_start", "capture_end_exclusive"):
        if receipt[field] != request[field]:
            raise ValueError("Complete search scope differs")
    ports, sensors = request["port_ids"], request["sensors"]
    if (
        not ports
        or not sensors
        or len(set(ports)) != len(ports)
        or len(set(sensors)) != len(sensors)
        or not set(sensors).issubset(SENSORS)
        or any(
            not isinstance(port, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", port)
            for port in ports
        )
    ):
        raise ValueError("Complete search scopes are invalid")
    start, end = utc_text(request["capture_start"]), utc_text(request["capture_end_exclusive"])
    cutoff, completed = utc_text(request["publication_cutoff"]), utc_text(discovery["completed_at"])
    if not start < end <= cutoff <= completed:
        raise ValueError("Complete search dates are inconsistent")
    expected = {(port, sensor) for port in ports for sensor in sensors}
    queries = discovery["queries"]
    actual = {(query["port_id"], query["sensor"]) for query in queries}
    if actual != expected or len(queries) != len(expected):
        raise ValueError("Complete search is missing required queries")
    for query in queries:
        if (
            query["complete"] is not True
            or utc_text(query["capture_start"]) != start
            or utc_text(query["capture_end_exclusive"]) != end
            or not cutoff <= utc_text(query["completed_at"]) <= completed
            or not isinstance(query["pages"], int)
            or query["pages"] < 1
            or query["pages"] != len(query["page_evidence"])
            or query["page_evidence_sha256"] != _digest(query["page_evidence"])
            or query["page_evidence"][-1].get("next_link")
            or not isinstance(query["products"], int)
            or query["products"] < 0
            or (
                query["reported_count"] is not None and query["reported_count"] != query["products"]
            )
        ):
            raise ValueError("Complete search query evidence is inconsistent")
    public_queries = [
        {key: value for key, value in query.items() if key in QUERY_PUBLIC_FIELDS}
        for query in queries
    ]
    if receipt["queries"] != public_queries:
        raise ValueError("Complete search receipt differs from query evidence")
    return {
        "scopes": expected,
        "last_complete_search_at": completed,
        "searched_capture_start": start,
        "searched_capture_end_exclusive": end,
    }


def freshness_report(store: Store) -> dict:
    """Cheap operator freshness; a useful zone does not imply a full-port pass."""
    watermarks, errors = {}, []
    path = store.data_dir / "refresh/watermarks.json"
    if path.exists():
        try:
            value = json.loads(
                confined_path(store.data_dir, path.relative_to(store.data_dir)).read_text()
            )
            if value.get("schema") != SCHEMA:
                raise ValueError("Unknown watermark schema")
            cache = {}
            for item in value["items"]:
                try:
                    key, run_id = (item["port_id"], item["sensor"]), item["run_id"]
                    if not isinstance(run_id, str):
                        raise ValueError("Invalid search run ID")
                    if run_id not in cache:
                        cache[run_id] = None
                        cache[run_id] = _verified_complete_search(store, run_id)
                    evidence = cache[run_id]
                    dates = {
                        field: utc_text(item[field])
                        for field in (
                            "last_complete_search_at",
                            "searched_capture_start",
                            "searched_capture_end_exclusive",
                        )
                    }
                    if (
                        not evidence
                        or key not in evidence["scopes"]
                        or any(dates[field] != evidence[field] for field in dates)
                    ):
                        raise ValueError("Watermark differs from complete search evidence")
                    watermarks[key] = dates
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    errors.append("freshness_search_evidence_unavailable")
        except (OSError, ValueError, KeyError, TypeError):
            watermarks = {}
            errors.append("freshness_watermark_unavailable")
    ownership, campaign_errors = campaign_index(store.data_dir)
    errors.extend(campaign_errors)
    with closing(store.connect()) as tx:
        tx.execute("BEGIN")
        acquisitions = [
            dict(row)
            for row in tx.execute(
                "SELECT acquisition_key,sensor,captured_at,port_ids_json,metadata_json FROM acquisitions"
            )
        ]
        observations = [
            dict(row)
            for row in tx.execute(
                "SELECT acquisition_key,port_id,sensor,captured_at,processed_at,status,review_status,observable_fraction FROM observations"
            )
        ]
    ports = {"prq", "pbr", "stc"}
    rows = defaultdict(list)
    for acquisition in acquisitions:
        metadata = json.loads(acquisition["metadata_json"])
        if metadata.get("context_only") or metadata.get("purpose") == "auxiliary_context":
            continue
        for port in json.loads(acquisition["port_ids_json"]):
            ports.add(port)
            rows[port, acquisition["sensor"]].append(acquisition)
    items = []
    for port in sorted(ports):
        for sensor in SENSORS:
            selected = rows[port, sensor]
            scoped = [
                obs for obs in observations if obs["port_id"] == port and obs["sensor"] == sensor
            ]
            processed = [
                obs for obs in scoped if obs["status"] in {"usable", "partial", "unusable"}
            ]
            useful = [
                obs
                for obs in processed
                if obs["review_status"] != "rejected"
                and obs["status"] in {"usable", "partial"}
                and (obs["observable_fraction"] or 0) >= 0.9
            ]
            complete_keys = {obs["acquisition_key"] for obs in processed}
            unfinished_keys = {
                obs["acquisition_key"]
                for obs in scoped
                if obs["status"] in {"pending", "processing", "failed"}
            }
            pending, unavailable = 0, 0
            for acq in selected:
                states = {entry["status"] for entry in ownership.get(acq["acquisition_key"], [])}
                if states - {"complete", "unavailable"}:
                    pending += 1
                elif "unavailable" in states and "complete" not in states:
                    unavailable += 1
                elif acq["acquisition_key"] in unfinished_keys:
                    pending += 1
                elif acq["acquisition_key"] not in complete_keys:
                    pending += 1

            def latest(values, field):
                return max((v[field] for v in values if v.get(field)), default=None)

            items.append(
                {
                    "port_id": port,
                    "sensor": sensor,
                    "last_complete_search_at": None,
                    "searched_capture_start": None,
                    "searched_capture_end_exclusive": None,
                    **watermarks.get((port, sensor), {}),
                    "last_catalog_capture_at": latest(selected, "captured_at"),
                    "last_processed_capture_at": latest(processed, "captured_at"),
                    "last_processed_at": latest(processed, "processed_at"),
                    "last_useful_capture_at": latest(useful, "captured_at"),
                    "pending_acquisitions": pending,
                    "unavailable_acquisitions": unavailable,
                }
            )
    return {
        "schema": "catalog-freshness-v1",
        "items": items,
        "errors": sorted(set(errors)),
        "limits": [
            "A complete search is relative to its recorded capture interval and provider filters.",
            "Late publications outside the configured lookback require an explicit wider inventory.",
            "Useful means at least one non-rejected zone with observable fraction >=0.90; not full-port coverage or detection accuracy.",
            "Unavailable acquisitions remain explicit exceptions, not zero vessels.",
        ],
    }
