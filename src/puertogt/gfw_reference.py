"""Bounded GFW port-visit corroboration; never labels or automatic validation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from shapely.geometry import shape

ENDPOINT = "https://gateway.api.globalfishingwatch.org/v3/events"
DATASET = "public-global-port-visits-events:v5.0"
PORTS_VERSION = "2026-10-20-provisional-v1"
TRAIN_START, TRAIN_END = "2025-01-01", "2025-07-01"
PORT_IDS = ("prq", "pbr", "stc")
DEFAULT_LIMITS = {
    "page_size": 200,
    "max_pages": 30,
    "max_events": 10_000,
    "max_page_bytes": 1_048_576,
    "max_total_bytes": 20_971_520,
    "request_timeout_seconds": 20,
    "max_seconds": 120,
}
INTERPRETATION = (
    "Port visits corroborate a vessel visit only. They are not instantaneous positions at "
    "Sentinel capture time, vessel truth boxes, or evidence of absence. No labels are imported."
)


class GFWError(ValueError):
    """Only fixed, credential-free messages may be exposed to callers."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local(workspace: Path, relative: str) -> Path:
    root = workspace.resolve()
    target = root / relative
    for part in [target, *target.parents]:
        if part == root:
            break
        if part.is_symlink():
            raise GFWError("unsafe_path", "GFW local storage cannot use symbolic links.")
    return target


def _references(workspace: Path) -> Path:
    return _local(workspace, "data/references/gfw")


def _secret(workspace: Path) -> Path:
    return _local(workspace, "data/secrets/gfw-token")


def _valid_token(token: str) -> bool:
    return bool(token) and len(token) <= 8192 and all(33 <= ord(c) <= 126 for c in token)


def configure_token(workspace: Path, token: str, *, replace=False) -> dict:
    """The CLI supplies this through getpass, never through an argument or environment."""
    if not _valid_token(token):
        raise GFWError("invalid_token", "The token must be nonempty ASCII without whitespace.")
    path = _secret(workspace)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.parent.chmod(0o700)
    if path.exists() and not replace:
        raise GFWError("credential_exists", "Credential exists; use configure --replace explicitly.")
    if replace:
        fd, temporary = tempfile.mkstemp(prefix=".gfw-token-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(token.encode())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(token.encode())
    return {"status": "configured", "credential_file": "data/secrets/gfw-token", "network": False}


def _read_token(workspace: Path) -> str:
    path = _secret(workspace)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise GFWError("missing_token", "No GFW token. Run configure after verifying your account.") from None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid()):
            raise GFWError("unsafe_token_file", "The GFW token must be an owned regular file with mode 600.")
        raw = handle.read(8193)
    try:
        token = raw.decode("ascii").rstrip("\r\n")
    except UnicodeDecodeError:
        raise GFWError("invalid_token", "The stored GFW token has an invalid format.") from None
    if not _valid_token(token):
        raise GFWError("invalid_token", "The stored GFW token has an invalid format.")
    return token


def status(workspace: Path) -> dict:
    """Local inspection only: presence never implies working API permissions."""
    try:
        _read_token(workspace)
    except (GFWError, OSError) as exc:
        return {"status": exc.code if isinstance(exc, GFWError) else "credential_unreadable",
                "configured": False, "network": False, "api_access_verified": False}
    return {"status": "configured_not_verified", "configured": True,
            "network": False, "api_access_verified": False}


def _limits(values: dict) -> dict:
    if set(values) != set(DEFAULT_LIMITS) or any(
        type(value) is not int or not 1 <= value <= DEFAULT_LIMITS[key]
        for key, value in values.items()
    ):
        raise GFWError("invalid_budget", "Budgets must be positive integers within the fixed ceilings.")
    if values["max_total_bytes"] < values["max_page_bytes"]:
        raise GFWError("invalid_budget", "Total byte budget must cover at least one page.")
    return dict(values)


def build_plan(ports_path: Path, *, start=TRAIN_START, end=TRAIN_END, limits=None) -> dict:
    """Freeze three V1 bounding polygons and a date subset of the original TRAIN window."""
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (ValueError, TypeError):
        raise GFWError("invalid_scope", "Use ISO dates inside January-June 2025 TRAIN.") from None
    if not date.fromisoformat(TRAIN_START) <= first < last <= date.fromisoformat(TRAIN_END):
        raise GFWError("invalid_scope", "Only January-June 2025 TRAIN is permitted; CAL/TEST are closed.")
    raw = ports_path.read_bytes()
    ports = json.loads(raw)
    if ports.get("version") != PORTS_VERSION:
        raise GFWError("invalid_scope", "This connector requires the frozen provisional V1 port zones.")
    queries = []
    for port in PORT_IDS:
        features = [f for f in ports["features"] if f["properties"].get("port_id") == port]
        if not features or any(f["properties"].get("version") != PORTS_VERSION for f in features):
            raise GFWError("invalid_scope", "All three ports require explicit V1 zone geometry.")
        geometries = [shape(f["geometry"]) for f in features]
        if any(g.is_empty or not g.is_valid or g.geom_type not in {"Polygon", "MultiPolygon"}
               for g in geometries):
            raise GFWError("invalid_scope", "Port zones must contain valid polygon geometry.")
        bounds = [g.bounds for g in geometries]
        west, south = min(b[0] for b in bounds), min(b[1] for b in bounds)
        east, north = max(b[2] for b in bounds), max(b[3] for b in bounds)
        if (not all(math.isfinite(v) for v in (west, south, east, north))
                or not -180 <= west < east <= 180 or not -90 <= south < north <= 90
                or east - west > 1 or north - south > 1):
            raise GFWError("invalid_scope", "Port bounding polygon exceeds the bounded WGS84 scope.")
        polygon = {"type": "Polygon", "coordinates": [
            [[west, south], [east, south], [east, north], [west, north], [west, south]]
        ]}
        queries.append({"port_id": port, "source_zone_ids": sorted(
            f["properties"]["zone_id"] for f in features
        ), "body": {"datasets": [DATASET], "startDate": start, "endDate": end,
                    "geometry": polygon}})
    return {"schema": "puertogt-gfw-train-plan-v1", "method": "POST", "endpoint": ENDPOINT,
            "split": "train", "window": {"start": start, "end_exclusive": end},
            "ports_version": PORTS_VERSION, "ports_sha256": hashlib.sha256(raw).hexdigest(),
            "limits": _limits(DEFAULT_LIMITS if limits is None else limits), "queries": queries,
            "interpretation": INTERPRETATION}


def _write_json(path: Path, value) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(_bytes(value) + b"\n")


def freeze_plan(workspace: Path, ports_path: Path, **options) -> dict:
    plan = build_plan(ports_path, **options)
    digest = _digest(plan)
    directory = _references(workspace) / "plans"
    if directory.is_symlink():
        raise GFWError("unsafe_path", "GFW plans cannot use symbolic links.")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (digest + ".json")
    _write_json(path, {"sha256": digest, "plan": plan})
    return {"status": "plan_frozen", "path": str(path), "sha256": digest, "network": False}


def read_plan(workspace: Path, path: Path, expected_sha256: str, ports_path: Path) -> dict:
    try:
        path.resolve().relative_to(_references(workspace).resolve())
        envelope = json.loads(path.read_text())
        plan = envelope["plan"]
        rebuilt = build_plan(ports_path, start=plan["window"]["start"],
                             end=plan["window"]["end_exclusive"], limits=plan["limits"])
        valid = (expected_sha256 == envelope["sha256"] == _digest(plan) and plan == rebuilt)
    except (KeyError, TypeError, ValueError, OSError):
        raise GFWError("invalid_plan", "Frozen plan, scope, source hash or expected digest is invalid.") from None
    if not valid:
        raise GFWError("invalid_plan", "Frozen plan, scope, source hash or expected digest is invalid.")
    return plan


def _page(raw: bytes, token: str, offset: int, limit: int, previous_total, seen: set) -> tuple:
    try:
        value = json.loads(raw)
        if token.encode() in raw or token.encode() in _bytes(value):
            raise GFWError("credential_echo", "Response discarded because it echoed a credential.")
        entries, total, following = value["entries"], value["total"], value["nextOffset"]
        if (type(total) is not int or total < 0 or not isinstance(entries, list)
                or len(entries) > limit or offset + len(entries) > total
                or value.get("offset", offset) != offset
                or value.get("limit", limit) != limit
                or (previous_total is not None and previous_total != total)):
            raise ValueError
        ids = [entry["id"] for entry in entries]
        if (any(not isinstance(entry, dict) or entry.get("type") != "port_visit"
                for entry in entries) or any(not isinstance(i, str) or not i for i in ids)
                or len(set(ids)) != len(ids) or seen.intersection(ids)):
            raise ValueError
        consumed = offset + len(entries)
        done = consumed == total
        if done:
            if following is not None and (type(following) is not int
                                         or not consumed <= following <= offset + limit):
                raise ValueError
        elif not entries or type(following) is not int or following != consumed:
            raise ValueError
        return value, total, following, done, set(ids)
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        if isinstance(exc, GFWError):
            raise
        raise GFWError("invalid_pagination", "Response schema, event type or pagination is inconsistent.") from None


def fetch(workspace: Path, plan_path: Path, expected_sha256: str, ports_path: Path,
          *, transport=None) -> dict:
    """Explicit bounded fetch. A supplied transport is for offline tests, not a URL override."""
    token = _read_token(workspace)  # Fail before client creation, network or receipt creation.
    plan = read_plan(workspace, plan_path, expected_sha256, ports_path)
    limits = plan["limits"]
    runs = _references(workspace) / "runs"
    if runs.is_symlink():
        raise GFWError("unsafe_path", "GFW runs cannot use symbolic links.")
    directory = runs / (expected_sha256[:16] + "-" + uuid4().hex)
    directory.mkdir(parents=True)
    receipt = {"schema": "puertogt-gfw-receipt-v1", "started_at": _now(),
               "plan_sha256": expected_sha256, "split": "train", "endpoint": ENDPOINT,
               "dataset": DATASET, "window": plan["window"], "ports_sha256": plan["ports_sha256"],
               "limits": limits, "pages": [], "completed_ports": [], "received_bytes": 0,
               "status": "running", "interpretation": INTERPRETATION,
               "labels_imported": False, "automatic_validation": False}
    _write_json(directory / "plan.json", {"sha256": expected_sha256, "plan": plan})
    started = time.monotonic()
    try:
        with httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                          timeout=limits["request_timeout_seconds"]) as client:
            for query in plan["queries"]:
                offset, total, seen = 0, None, set()
                while True:
                    remaining = limits["max_seconds"] - (time.monotonic() - started)
                    if remaining <= 0:
                        raise GFWError("time_budget", "Stopped at the frozen time budget.")
                    if len(receipt["pages"]) >= limits["max_pages"]:
                        raise GFWError("page_budget", "Stopped at the frozen page budget; coverage is partial.")
                    remaining_events = limits["max_events"] - sum(p["entries"] for p in receipt["pages"])
                    if remaining_events <= 0:
                        raise GFWError("event_budget", "Stopped at the frozen event budget; coverage is partial.")
                    page_limit = min(limits["page_size"], remaining_events)
                    with client.stream("POST", ENDPOINT,
                                       params={"offset": offset, "limit": page_limit},
                                       json=query["body"],
                                       headers={"Authorization": "Bearer " + token,
                                                "Accept": "application/json"},
                                       timeout=min(remaining, limits["request_timeout_seconds"])) as response:
                        if response.status_code not in (200, 201):
                            codes = {401: ("unauthorized", "401: verify the locally stored GFW token."),
                                     403: ("forbidden", "403: this account lacks dataset permission."),
                                     429: ("rate_limited", "429: quota reached; no automatic retry.")}
                            code, message = codes.get(response.status_code, (
                                "http_error", f"GFW returned HTTP {response.status_code}; no redirect or retry."
                            ))
                            raise GFWError(code, message)
                        raw = bytearray()
                        for chunk in response.iter_bytes():
                            receipt["received_bytes"] += len(chunk)
                            if (len(raw) + len(chunk) > limits["max_page_bytes"]
                                    or receipt["received_bytes"] > limits["max_total_bytes"]):
                                raise GFWError("byte_budget", "Stopped at the frozen byte budget.")
                            if time.monotonic() - started > limits["max_seconds"]:
                                raise GFWError("time_budget", "Stopped at the frozen time budget.")
                            raw.extend(chunk)
                    value, total, following, done, ids = _page(
                        bytes(raw), token, offset, page_limit, total, seen
                    )
                    name = f"{query['port_id']}-{offset:06d}.json"
                    _write_json(directory / name, value)
                    receipt["pages"].append({"port_id": query["port_id"], "offset": offset,
                                             "http_status": response.status_code, "limit": page_limit,
                                             "entries": len(value["entries"]), "total": total,
                                             "next_offset": following, "path": name,
                                             "stored_sha256": hashlib.sha256(
                                                 (directory / name).read_bytes()).hexdigest(),
                                             "response_sha256": hashlib.sha256(raw).hexdigest(),
                                             "response_bytes": len(raw)})
                    seen.update(ids)
                    if done:
                        receipt["completed_ports"].append(query["port_id"])
                        break
                    offset = following
        receipt["status"] = "complete"
    except GFWError as exc:
        receipt.update(status="stopped", reason=exc.code, message=str(exc))
    except httpx.HTTPError:
        receipt.update(status="stopped", reason="network_error",
                       message="GFW transport failed; request details and response text were suppressed.")
    receipt["finished_at"] = _now()
    receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
    _write_json(directory / "receipt.json", receipt)
    return {**receipt, "receipt_path": str(directory / "receipt.json")}
