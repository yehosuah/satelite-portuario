"""SQLite catalog, explicit UTC dates and relative, registered file paths.

Connections are short lived. Each writer uses ``BEGIN IMMEDIATE`` so checking
an invariant and changing its rows is one transaction, including across workers.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS acquisitions (
    acquisition_key TEXT PRIMARY KEY,
    scene_id TEXT NOT NULL, sensor TEXT NOT NULL, product_level TEXT NOT NULL,
    captured_at TEXT NOT NULL, published_at TEXT, discovered_at TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '', footprint_json TEXT NOT NULL DEFAULT '{}',
    source_assets_json TEXT NOT NULL DEFAULT '{}', port_ids_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}', data_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS acquisitions_capture ON acquisitions(captured_at, acquisition_key);
CREATE TABLE IF NOT EXISTS observations (
    observation_id TEXT PRIMARY KEY,
    acquisition_key TEXT NOT NULL REFERENCES acquisitions(acquisition_key),
    zone_id TEXT NOT NULL, recipe_version TEXT NOT NULL,
    port_id TEXT, sensor TEXT NOT NULL, captured_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','processing','usable','partial','unusable','failed')),
    review_status TEXT NOT NULL DEFAULT 'provisional'
        CHECK(review_status IN ('provisional','reviewed','rejected')),
    observable_fraction REAL CHECK(observable_fraction BETWEEN 0 AND 1),
    quality_flags_json TEXT NOT NULL DEFAULT '[]', detection_count INTEGER,
    processed_at TEXT, model_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}', data_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(acquisition_key, zone_id, recipe_version),
    CHECK(detection_count IS NULL OR detection_count >= 0),
    CHECK(status NOT IN ('unusable','failed','pending','processing') OR detection_count IS NULL)
);
CREATE INDEX IF NOT EXISTS observations_query ON observations(captured_at, observation_id);
CREATE INDEX IF NOT EXISTS observations_zone ON observations(zone_id, sensor, captured_at);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL UNIQUE REFERENCES observations(observation_id),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','running','retry','paused','succeeded','failed','input_failed')),
    attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 5,
    queued_at TEXT NOT NULL, not_before TEXT NOT NULL, started_at TEXT, finished_at TEXT,
    worker_id TEXT, lease_token TEXT, lease_expires_at TEXT,
    error_code TEXT, error_message TEXT, provider TEXT NOT NULL DEFAULT '',
    estimated_bytes INTEGER NOT NULL DEFAULT 0,
    quota_key TEXT, quota_units REAL NOT NULL DEFAULT 0,
    data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS one_running_job ON jobs((1)) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(status, not_before, queued_at, job_id);
CREATE TABLE IF NOT EXISTS models (
    model_id TEXT PRIMARY KEY, task TEXT NOT NULL, sensor TEXT,
    version TEXT NOT NULL, weights_path TEXT, sha256 TEXT,
    created_at TEXT NOT NULL, data_json TEXT NOT NULL,
    CHECK((weights_path IS NULL) = (sha256 IS NULL))
);
CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    observation_id TEXT REFERENCES observations(observation_id),
    model_id TEXT REFERENCES models(model_id),
    path TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
    mime_type TEXT NOT NULL DEFAULT 'application/octet-stream', crs TEXT,
    bounds_json TEXT, attribution TEXT, created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL DEFAULT 'ready'
        CHECK(state = 'ready')
);
CREATE TABLE IF NOT EXISTS detections (
    detection_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES observations(observation_id),
    model_id TEXT, geometry_json TEXT NOT NULL, detection_score REAL,
    family TEXT, length_m REAL, width_m REAL,
    quality_json TEXT NOT NULL DEFAULT '{}', evidence_json TEXT NOT NULL DEFAULT '[]',
    review_status TEXT NOT NULL DEFAULT 'provisional'
        CHECK(review_status IN ('provisional','reviewed','rejected')),
    created_at TEXT NOT NULL, data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS detections_observation ON detections(observation_id, detection_id);
CREATE TABLE IF NOT EXISTS changes (
    change_id TEXT PRIMARY KEY, observation_id TEXT REFERENCES observations(observation_id),
    geometry_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'candidate'
        CHECK(status IN ('candidate','persistent','reverted','rejected')),
    review_status TEXT NOT NULL DEFAULT 'provisional'
        CHECK(review_status IN ('provisional','reviewed','rejected')),
    created_at TEXT NOT NULL, data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS revisions (
    revision_id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
    previous_revision_id TEXT REFERENCES revisions(revision_id),
    proposal_json TEXT NOT NULL, correction_json TEXT NOT NULL,
    author TEXT NOT NULL, created_at TEXT NOT NULL, data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS revisions_entity ON revisions(entity_type, entity_id, created_at);
CREATE TABLE IF NOT EXISTS quotas (
    quota_key TEXT PRIMARY KEY, period TEXT NOT NULL,
    limit_units REAL NOT NULL CHECK(limit_units >= 0),
    used_units REAL NOT NULL DEFAULT 0 CHECK(used_units >= 0)
);
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime | str | None = None) -> str:
    """Normalize aware timestamps with fixed precision for SQLite comparisons."""
    if value is None:
        value = utc_now()
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware timestamp is required")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    return dict(value)


def json_text(value: Any) -> str:
    def default(item: Any) -> Any:
        if isinstance(item, datetime):
            return utc_text(item)
        if isinstance(item, Path):
            return str(item)
        if hasattr(item, "value"):
            return item.value
        raise TypeError(f"Cannot serialize {type(item).__name__}")

    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=default)


def relative_path(value: str | Path) -> str:
    """Accept portable POSIX relative paths only, without traversal or symlinks."""
    text = str(value)
    path = PurePosixPath(text)
    if not text or "\\" in text or "\x00" in text or path.is_absolute():
        raise ValueError("Asset paths must be relative POSIX paths")
    if any(part in ("", ".", "..") for part in text.split("/")) or ":" in path.parts[0]:
        raise ValueError("Unsafe asset path")
    return path.as_posix()


def confined_path(root: Path, value: str | Path, *, must_exist: bool = True) -> Path:
    """Resolve a registered path and reject symlinks at every component."""
    name = relative_path(value)
    root = root.resolve()
    current = root
    for part in PurePosixPath(name).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symlinks are not supported for stored evidence")
    if not current.resolve().is_relative_to(root):
        raise ValueError("Path escapes the data directory")
    if must_exist and not current.is_file():
        raise FileNotFoundError(current)
    return current


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Store:
    def __init__(self, db_path: Path | str, data_dir: Path | str | None = None) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.data_dir = Path(data_dir).expanduser().resolve() if data_dir else self.db_path.parent
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with closing(self.connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(SCHEMA)
            versions = [
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            ]
            if versions and max(versions) > SCHEMA_VERSION:
                raise ValueError("Catalog schema is newer than this service")
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utc_text()),
            )

    def register_scene(self, scene: Any, *, connection: sqlite3.Connection | None = None) -> str:
        value = as_dict(scene)
        key = value["acquisition_key"]
        if connection is None:
            with self.transaction() as tx:
                return self.register_scene(value, connection=tx)
        value["captured_at"] = utc_text(value["captured_at"])
        value["published_at"] = (
            utc_text(value["published_at"]) if value.get("published_at") else None
        )
        value["discovered_at"] = utc_text(value.get("discovered_at"))
        existing = connection.execute(
            "SELECT * FROM acquisitions WHERE acquisition_key = ?", (key,)
        ).fetchone()
        if existing:
            # SAFE names carry whole seconds, while the catalog includes fractional
            # seconds. Preserve the catalog timestamp for the same keyed capture.
            same_second = (
                existing["captured_at"].split(".")[0] == value["captured_at"].split(".")[0]
            )
            if (
                not same_second
                or existing["sensor"] != value["sensor"]
                or existing["product_level"] != value["product_level"]
            ):
                raise ValueError(
                    "Acquisition key conflicts with capture time, sensor or product level"
                )
            merged = json.loads(existing["data_json"])
            merged["source_assets"] = {
                **merged.get("source_assets", {}),
                **value.get("source_assets", {}),
            }
            merged["port_ids"] = sorted(
                set(merged.get("port_ids", [])) | set(value.get("port_ids", []))
            )
            from shapely.geometry import mapping, shape

            if merged.get("footprint") and value.get("footprint"):
                merged["footprint"] = mapping(
                    shape(merged["footprint"]).union(shape(value["footprint"]))
                )
            else:
                merged["footprint"] = value.get("footprint") or merged.get("footprint", {})
            old_metadata, new_metadata = merged.get("metadata", {}), value.get("metadata", {})
            representations = {
                json_text(item): item
                for item in [
                    *old_metadata.get("representations", []),
                    *new_metadata.get("representations", []),
                ]
            }
            merged["metadata"] = {**old_metadata, **new_metadata}
            if representations:
                merged["metadata"]["representations"] = list(representations.values())
            connection.execute(
                "UPDATE acquisitions SET source_assets_json=?, port_ids_json=?, footprint_json=?, metadata_json=?, data_json=? WHERE acquisition_key=?",
                (
                    json_text(merged["source_assets"]),
                    json_text(merged["port_ids"]),
                    json_text(merged["footprint"]),
                    json_text(merged["metadata"]),
                    json_text(merged),
                    key,
                ),
            )
            return key
        connection.execute(
            """INSERT INTO acquisitions(acquisition_key,scene_id,sensor,product_level,captured_at,
            published_at,discovered_at,provider,footprint_json,source_assets_json,port_ids_json,
            metadata_json,data_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                key,
                value["scene_id"],
                value["sensor"],
                value["product_level"],
                value["captured_at"],
                value["published_at"],
                value["discovered_at"],
                value.get("provider", ""),
                json_text(value.get("footprint", {})),
                json_text(value.get("source_assets", {})),
                json_text(value.get("port_ids", [])),
                json_text(value.get("metadata", {})),
                json_text(value),
            ),
        )
        return key

    def get_observation(self, observation_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM observations WHERE observation_id=?", (observation_id,)
            ).fetchone()
        return self.decode_observation(row) if row else None

    @staticmethod
    def decode_observation(row: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(row)
        extra = json.loads(value.pop("data_json", "{}"))
        value["quality_flags"] = json.loads(value.pop("quality_flags_json", "[]"))
        value["metadata"] = json.loads(value.pop("metadata_json", "{}"))
        return {**extra, **value}

    def register_model(self, manifest: Any, *, weights_path: str | Path | None = None) -> str:
        value = as_dict(manifest)
        model_id = value.get("model_id") or str(uuid.uuid4())
        weights_path = weights_path or value.get("weights_path")
        digest = None
        if weights_path:
            weights_path = relative_path(weights_path)
            digest = sha256_file(confined_path(self.data_dir, weights_path))
            supplied = value.get("sha256") or value.get("weights_sha256")
            if supplied and supplied != digest:
                raise ValueError("Model weight checksum does not match its manifest")
            value["weights_path"] = weights_path
            value["weights_sha256"] = digest
        value["model_id"] = model_id
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT data_json FROM models WHERE model_id=?", (model_id,)
            ).fetchone()
            if existing:
                if json.loads(existing["data_json"]) != value:
                    raise ValueError(
                        "Model IDs are immutable; assign a new ID to changed weights or policy"
                    )
                return model_id
            connection.execute(
                "INSERT INTO models(model_id,task,sensor,version,weights_path,sha256,created_at,data_json) VALUES (?,?,?,?,?,?,?,?)",
                (
                    model_id,
                    value.get("task", "detection"),
                    value.get("sensor"),
                    value.get("version", model_id),
                    weights_path,
                    digest,
                    utc_text(),
                    json_text(value),
                ),
            )
        return model_id

    def register_asset(
        self,
        path: str | Path,
        *,
        observation_id: str | None = None,
        model_id: str | None = None,
        asset_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        if connection is None:
            with self.transaction() as tx:
                return self.register_asset(
                    path,
                    observation_id=observation_id,
                    model_id=model_id,
                    asset_id=asset_id,
                    metadata=metadata,
                    connection=tx,
                )
        path = relative_path(path)
        source = confined_path(self.data_dir, path)
        metadata = dict(metadata or {})
        digest = sha256_file(source)
        asset_id = asset_id or hashlib.sha256(f"{path}:{digest}".encode()).hexdigest()[:32]
        connection.execute(
            """INSERT INTO assets(asset_id,observation_id,model_id,path,sha256,size_bytes,mime_type,
               crs,bounds_json,attribution,created_at,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                asset_id,
                observation_id,
                model_id,
                path,
                digest,
                source.stat().st_size,
                metadata.get("mime_type", "application/octet-stream"),
                metadata.get("crs"),
                json_text(metadata["bounds"]) if metadata.get("bounds") is not None else None,
                metadata.get("attribution"),
                utc_text(),
                json_text(metadata),
            ),
        )
        return asset_id

    def asset_path(self, asset_id: str) -> Path:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT path FROM assets WHERE asset_id=?", (asset_id,)
            ).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return confined_path(self.data_dir, row["path"])

    def configure_quota(self, quota_key: str, period: str, limit_units: float) -> None:
        if limit_units < 0:
            raise ValueError("Quota limits must be nonnegative")
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO quotas(quota_key,period,limit_units,used_units) VALUES (?,?,?,0)
                ON CONFLICT(quota_key) DO UPDATE SET period=excluded.period,
                limit_units=excluded.limit_units,
                used_units=CASE WHEN quotas.period=excluded.period THEN quotas.used_units ELSE 0 END""",
                (quota_key, period, limit_units),
            )

    def snapshot(self, destination: Path | str) -> Path:
        destination = Path(destination)
        if destination.exists():
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = self.connect()
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
            target.execute("PRAGMA journal_mode = DELETE")
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("SQLite snapshot failed integrity check")
        finally:
            target.close()
            source.close()
        return destination
