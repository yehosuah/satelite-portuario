"""Audited recovery of an exact frozen S2 product from a pinned public mirror."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .history import HistoryLedger, _atomic_json, file_lock
from .preprocess import S2_BANDS
from .store import Store, sha256_file, utc_text


def _source_discovery(candidate: dict, planned: dict, candidate_sha256: str) -> dict:
    scene = planned["scene"]
    product = candidate.get("exact_product", "")
    parts = product.split("_")
    if (candidate.get("schema_version") != "historical-compatible-source-candidate-v1"
            or scene["sensor"] != "sentinel-2" or scene["product_level"] != "L1C"
            or candidate.get("acquisition_key") != scene["acquisition_key"]
            or product not in {item.get("name") for item in scene.get("metadata", {}).get("representations", [])}
            or len(parts) != 7 or parts[1] != "MSIL1C"):
        raise ValueError("Candidate must identify the exact original frozen L1C representation")
    expected_key = ":".join([parts[0], parts[1], parts[2], parts[4], parts[5]])
    baseline = parts[3].removeprefix("N")
    if (expected_key != scene["acquisition_key"] or len(baseline) != 4
            or candidate.get("processing_baseline") != baseline[:2] + "." + baseline[2:]):
        raise ValueError("Candidate acquisition identity or processing baseline differs")
    captured = datetime.strptime(parts[2], "%Y%m%dT%H%M%S")
    tile = parts[5].removeprefix("T")
    prefix = f"/tiles/{tile[:2]}/{tile[2]}/{tile[3:]}/{captured.year}/{captured.month}/{captured.day}/"
    sources = candidate.get("sources", {})
    if set(sources) != set(S2_BANDS):
        raise ValueError("Compatible recovery requires exactly nine pinned numeric bands")
    roots = set()
    for band, spec in sources.items():
        url = urlsplit(spec.get("url", ""))
        etag = spec.get("etag", "")
        if (url.scheme != "https" or url.netloc != "sentinel-s2-l1c.s3.eu-central-1.amazonaws.com"
                or not url.path.startswith(prefix) or not url.path.endswith("/" + band + ".jp2")
                or url.query or url.fragment or spec.get("format") != "jp2"
                or not isinstance(spec.get("size"), int) or isinstance(spec["size"], bool) or spec["size"] <= 0
                or len(etag) < 3 or not etag.startswith('"') or not etag.endswith('"')):
            raise ValueError("Every source requires an exact public JP2 path, size and ETag pin")
        roots.add(url.path.rsplit("/", 1)[0])
    if len(roots) != 1:
        raise ValueError("The nine bands must belong to the same source product")
    return {"source_product": product, "processing_baseline": candidate["processing_baseline"],
            "sources": sources, "recovery_provenance": {
                "candidate_sha256": candidate_sha256, "provider": candidate.get("provider"),
                "source_documentation": candidate.get("source_documentation"),
                "same_original_representation": True, "plan_replaced": False}}


def recover_compatible_source(catalog: Store, ledger: HistoryLedger, candidate_path: Path | str,
                              *, candidate_sha256: str, plan_sha256: str, reason: str) -> dict:
    """Pin exact replacement transport and reopen one unavailable acquisition.

    No network, catalog, plan, attempt or budget mutation occurs. Only a previously
    unavailable ledger state is reopened after a durable journal and source pins.
    The caller must first stop the campaign at its explicit PAUSE boundary.
    """
    candidate_path = Path(candidate_path)
    if not reason.strip() or sha256_file(candidate_path) != candidate_sha256:
        raise ValueError("Recovery requires a reason and a verified candidate checksum")
    candidate = json.loads(candidate_path.read_text())
    with file_lock(ledger.directory / "campaign.lock"), file_lock(catalog.data_dir / ".heavy-worker.lock"):
        plan_path = ledger.directory / "plan.json"
        if not (ledger.directory / "PAUSE").exists():
            raise ValueError("Pause the campaign before compatible source recovery")
        if sha256_file(plan_path) != plan_sha256:
            raise ValueError("Frozen historical plan checksum mismatch")
        plan = json.loads(plan_path.read_text())
        key = candidate["acquisition_key"]
        planned = next((item for item in plan["acquisitions"] if item["scene"]["acquisition_key"] == key), None)
        if planned is None:
            raise ValueError("Candidate acquisition is outside the frozen plan")
        discovery = _source_discovery(candidate, planned, candidate_sha256)
        identity = hashlib.sha256(key.encode()).hexdigest()[:24]
        source_path = ledger.directory / "work" / identity / "sources.json"
        journal_path = ledger.directory / "recoveries" / (identity + "-compatible-source.json")
        if source_path.exists() and json.loads(source_path.read_text()) != discovery:
            raise ValueError("Existing source pins differ; never overwrite a prior representation")
        with closing(ledger.store.connect()) as tx:
            row = tx.execute("SELECT * FROM history_acquisitions WHERE acquisition_key=?", (key,)).fetchone()
        if row is None or json.loads(row["plan_json"]) != planned:
            raise ValueError("Ledger acquisition differs from the frozen plan")
        current = {name: row[name] for name in row.keys() if name != "plan_json"}
        if journal_path.exists():
            journal = json.loads(journal_path.read_text())
            if (journal["plan_sha256"] != plan_sha256 or journal["candidate_sha256"] != candidate_sha256
                    or journal["acquisition_key"] != key or journal["source_discovery"] != discovery):
                raise ValueError("Recovery journal pins differ")
            if journal["status"] == "complete":
                if not source_path.exists() or sha256_file(source_path) != journal["sources_sha256"]:
                    raise ValueError("Completed recovery source evidence is missing or changed")
                return journal
        else:
            with closing(catalog.connect()) as tx:
                exists = tx.execute("SELECT 1 FROM observations WHERE acquisition_key=? LIMIT 1", (key,)).fetchone()
            if exists is not None:
                raise ValueError("Source recovery cannot alter an already observed acquisition")
            if (current["status"] != "unavailable"
                    or current["error_code"] not in {"FileNotFoundError", "PublicSourceUnavailable"}
                    or "GCS has no complete L1C nine-band representation" not in (current["error_message"] or "")
                    or current["attempts"] >= ledger.limits.max_attempts):
                raise ValueError("Only the verified public representation absence can be reopened")
            journal = {"schema_version": "history-compatible-source-recovery-v1", "status": "prepared",
                       "created_at": utc_text(), "reason": reason, "acquisition_key": key,
                       "plan_id": plan["plan_id"], "plan_sha256": plan_sha256,
                       "candidate_sha256": candidate_sha256, "ledger_before": current,
                       "source_discovery": discovery}
            _atomic_json(journal_path, journal)
        if not source_path.exists():
            _atomic_json(source_path, discovery)
        journal.update(status="sources_pinned", sources_sha256=sha256_file(source_path))
        _atomic_json(journal_path, journal)
        before = journal["ledger_before"]
        after = {**before, "status": "retry", "not_before": journal["created_at"],
                 "finished_at": None, "error_code": None, "error_message": None}
        with ledger.store.transaction() as tx:
            row = tx.execute("SELECT * FROM history_acquisitions WHERE acquisition_key=?", (key,)).fetchone()
            current = {name: row[name] for name in row.keys() if name != "plan_json"}
            if current not in (before, after):
                raise ValueError("Ledger state changed since recovery was prepared")
            if current == before:
                tx.execute("""UPDATE history_acquisitions SET status='retry',not_before=?,finished_at=NULL,
                    error_code=NULL,error_message=NULL WHERE acquisition_key=?""", (journal["created_at"], key))
        journal.update(status="complete", completed_at=utc_text())
        _atomic_json(journal_path, journal)
        _atomic_json(ledger.directory / "status.json", ledger.summary())
        return journal
