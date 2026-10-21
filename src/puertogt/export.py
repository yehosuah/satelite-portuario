"""Portable, verified snapshots and analysis exports of registered observations."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .contracts import PortableBundle
from .store import (
    SCHEMA_VERSION,
    Store,
    confined_path,
    json_text,
    relative_path,
    sha256_file,
    utc_text,
)

_SECRET_KEY = re.compile(r"password|passwd|secret|token|credential|authorization|api.?key|access.?key|private.?key|cookie", re.I)
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s\"']+")
_ASSIGNMENT = re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;\"']+")
_URL = re.compile(r"https?://[^\s\"'<>]+")
_RESERVED = {"manifest.json", "catalog.sqlite", "config.json", "catalog.sqlite-wal", "catalog.sqlite-shm", "catalog.sqlite-journal"}


def _safe_url(match: re.Match[str]) -> str:
    parsed = urlsplit(match.group(0))
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if parsed.port is not None:
        host += f":{parsed.port}"
    # Keep immutable public object versions; never copy signatures or access tokens.
    query = urlencode([(key, value) for key, value in parse_qsl(parsed.query)
                       if key in {"generation", "versionId", "version"}])
    return urlunsplit((parsed.scheme, host, parsed.path, query, ""))


def sanitize_configuration(value: Any) -> Any:
    """Remove credentials recursively; signed URLs are not portable credentials."""
    if isinstance(value, Mapping):
        return {str(key): sanitize_configuration(item) for key, item in value.items()
                if not _SECRET_KEY.search(str(key))}
    if isinstance(value, (list, tuple)):
        return [sanitize_configuration(item) for item in value]
    if isinstance(value, Path):
        return str(value) if not value.is_absolute() else "<local-path>"
    if isinstance(value, str):
        if "-----BEGIN" in value and "PRIVATE KEY-----" in value:
            return "<redacted>"
        value = _URL.sub(_safe_url, value)
        return _ASSIGNMENT.sub(r"\1=<redacted>", _BEARER.sub("Bearer <redacted>", value))
    return value


def _portable_name(name: str) -> str:
    name = relative_path(name)
    if any(part.lower() in {".env", ".credentials", "credentials.json", "id_rsa", "id_ed25519"}
           or part.lower().startswith(".env.") for part in Path(name).parts):
        raise ValueError("Credential files cannot be included in portable bundles")
    return name


def _clean_snapshot(path: Path) -> None:
    # secure_delete plus VACUUM also removes original secret bytes from free pages.
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA secure_delete = ON")
        for table in ("acquisitions", "observations", "jobs", "assets", "models", "detections", "changes", "revisions"):
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})") if row[1].endswith("_json")]
            for column in columns:
                for rowid, value in connection.execute(f"SELECT rowid,{column} FROM {table}").fetchall():
                    if value is not None:
                        cleaned = json_text(sanitize_configuration(json.loads(value)))
                        connection.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (cleaned, rowid))
        # A portable copy has no ownership relationship with processes on its source host.
        connection.execute("UPDATE jobs SET status='retry',not_before=? WHERE status='running'", (utc_text(),))
        connection.execute("UPDATE observations SET status='pending',detection_count=NULL WHERE status='processing'")
        connection.execute("UPDATE jobs SET worker_id=NULL,lease_token=NULL,lease_expires_at=NULL,error_message=error_code")
        connection.commit()
        connection.execute("VACUUM")


def _catalog_files(catalog: Path) -> dict[str, str]:
    """Validate structural integrity and obtain every referenced asset/weight hash."""
    with closing(sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Catalog failed integrity check")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("Catalog contains invalid references")
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')").fetchone():
            raise ValueError("Portable catalogs cannot contain triggers or views")
        version = connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise ValueError("Unsupported catalog schema version")
        files: dict[str, str] = {}
        for query in ("SELECT path AS path,sha256 FROM assets", "SELECT weights_path AS path,sha256 FROM models WHERE weights_path IS NOT NULL"):
            for row in connection.execute(query):
                name = _portable_name(row["path"])
                if name in _RESERVED:
                    raise ValueError("Evidence uses a reserved bundle path")
                if not re.fullmatch(r"[a-f0-9]{64}", row["sha256"]):
                    raise ValueError("Invalid registered file checksum")
                if name in files and files[name] != row["sha256"]:
                    raise ValueError("Inconsistent checksums for a referenced file")
                files[name] = row["sha256"]
        return files


def _publish_directory(staged: Path, destination: Path) -> None:
    if destination.is_symlink():
        raise ValueError("Destination cannot be a symlink")
    if destination.exists():
        # Empty, explicitly selected restore directories are allowed, existing data is not.
        if not destination.is_dir() or any(destination.iterdir()):
            raise FileExistsError(destination)
        destination.rmdir()
    staged.rename(destination)


def export_bundle(store: Store, destination: Path | str,
                  config: Mapping[str, Any] | None = None) -> PortableBundle:
    destination = Path(destination).expanduser().absolute()
    if destination.is_symlink() or destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.pending-", dir=destination.parent))
    try:
        snapshot = store.snapshot(stage / "catalog.sqlite")
        _clean_snapshot(snapshot)
        referenced = _catalog_files(snapshot)
        for name, expected in referenced.items():
            source = confined_path(store.data_dir, name)
            target = confined_path(stage, name, must_exist=False)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if sha256_file(target) != expected:
                raise ValueError(f"Registered file checksum mismatch: {name}")
        configuration = sanitize_configuration(dict(config or {}))
        configuration.update({"data_dir": ".", "database": "catalog.sqlite"})
        (stage / "config.json").write_text(json_text(configuration) + "\n", encoding="utf-8")
        names = ["catalog.sqlite", "config.json", *sorted(referenced)]
        manifest = PortableBundle(
            files={name: sha256_file(stage / name) for name in names},
            metadata={"schema_version": SCHEMA_VERSION, "catalog": "catalog.sqlite", "configuration": "config.json",
                      "file_sizes": {name: (stage / name).stat().st_size for name in names}},
        )
        (stage / "manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
        _publish_directory(stage, destination)
        return manifest
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def restore_bundle(bundle_path: Path | str, destination: Path | str) -> Store:
    source = Path(bundle_path).expanduser().absolute()
    destination = Path(destination).expanduser().absolute()
    if source.is_symlink() or destination.is_symlink():
        raise ValueError("Bundle and destination must not be symlinks")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise FileExistsError(destination)
    manifest = PortableBundle.model_validate_json(confined_path(source, "manifest.json").read_text(encoding="utf-8"))
    if manifest.format_version != 1 or manifest.metadata.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported portable bundle format")
    if "catalog.sqlite" not in manifest.files or "config.json" not in manifest.files:
        raise ValueError("Bundle must include its catalog and safe configuration")
    for name, checksum in manifest.files.items():
        _portable_name(name)
        if name in {"manifest.json", "catalog.sqlite-wal", "catalog.sqlite-shm", "catalog.sqlite-journal"}:
            raise ValueError("Forbidden portable path")
        if not re.fullmatch(r"[a-f0-9]{64}", checksum):
            raise ValueError("Invalid file checksum")
        file = confined_path(source, name)
        if sha256_file(file) != checksum:
            raise ValueError(f"File checksum mismatch: {name}")
    # Do not open a SQLite database with an untracked adjacent journal/WAL file.
    for suffix in ("-wal", "-shm", "-journal"):
        if (source / f"catalog.sqlite{suffix}").exists() or (source / f"catalog.sqlite{suffix}").is_symlink():
            raise ValueError("Portable catalogs must be closed SQLite snapshots")
    references = _catalog_files(source / "catalog.sqlite")
    for name, checksum in references.items():
        if manifest.files.get(name) != checksum:
            raise ValueError(f"Registered file is absent or its checksum differs: {name}")
    expected = {"catalog.sqlite", "config.json", *references}
    if set(manifest.files) != expected:
        raise ValueError("Bundle contains files not referenced by its catalog")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.pending-", dir=destination.parent))
    try:
        for name, checksum in manifest.files.items():
            target = confined_path(stage, name, must_exist=False)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(confined_path(source, name), target)
            if sha256_file(target) != checksum:
                raise ValueError(f"File checksum changed during restore: {name}")
        # Validate copied bytes before the directory becomes usable.
        _catalog_files(stage / "catalog.sqlite")
        _publish_directory(stage, destination)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return Store(destination / "catalog.sqlite", data_dir=destination)


def _features(store: Store, entity: str) -> list[dict[str, Any]]:
    if entity not in {"detections", "changes"}:
        raise ValueError("Export entity must be detections or changes")
    with closing(store.connect()) as connection:
        rows = connection.execute(
            f"SELECT records.* FROM {entity} records LEFT JOIN observations parent USING(observation_id) "
            "WHERE records.review_status != 'rejected' AND (parent.observation_id IS NULL OR "
            "(parent.review_status != 'rejected' AND parent.status IN ('usable','partial')))"
            + (" AND records.status != 'rejected'" if entity == "changes" else "")
            + (" ORDER BY records.detection_id" if entity == "detections" else " ORDER BY records.change_id")
        ).fetchall()
    features = []
    for row in rows:
        value = dict(row)
        extra = json.loads(value.pop("data_json", "{}"))
        geometry = json.loads(value.pop("geometry_json"))
        identifier = value["detection_id" if entity == "detections" else "change_id"]
        for field in list(value):
            if field.endswith("_json"):
                value[field[:-5]] = json.loads(value.pop(field))
        extra.pop("geometry", None)
        features.append({"type": "Feature", "id": identifier, "geometry": geometry,
                         "properties": sanitize_configuration({**extra, **value})})
    return features


def _atomic_write(path: Path, writer: Any) -> Path:
    if path.is_symlink():
        raise ValueError("Export destination cannot be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.pending-", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        writer(temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def export_geojson(store: Store, destination: Path | str, *, entity: str = "detections") -> Path:
    collection = {"type": "FeatureCollection", "features": _features(store, entity)}
    return _atomic_write(Path(destination), lambda target: target.write_text(json_text(collection) + "\n", encoding="utf-8"))


def export_parquet(store: Store, destination: Path | str, *, entity: str = "detections") -> Path:
    """Export WGS84 geometry as GeoJSON text; nested metadata is JSON text too."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = []
    for feature in _features(store, entity):
        row = {"id": feature["id"], "geometry": json_text(feature["geometry"]), **feature["properties"]}
        rows.append({key: json_text(value) if isinstance(value, (dict, list)) else value for key, value in row.items()})
    table = pa.Table.from_pylist(rows) if rows else pa.table({"id": pa.array([], pa.string()), "geometry": pa.array([], pa.string())})
    table = table.replace_schema_metadata({b"geometry_encoding": b"GeoJSON", b"crs": b"OGC:CRS84"})
    return _atomic_write(Path(destination), lambda target: pq.write_table(table, target))
