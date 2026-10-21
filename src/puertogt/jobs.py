"""Recoverable single-worker queue with fenced leases and atomic evidence files.

The OS lock remains held for the entire heavy job. A stale timestamp alone never
permits another process to steal running work; after process exit the lock is
released by the OS and expired jobs can be retried safely.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .store import (
    Store,
    as_dict,
    confined_path,
    json_text,
    relative_path,
    sha256_file,
    utc_now,
    utc_text,
)


class LeaseLost(RuntimeError):
    """The caller cannot publish or finish work owned by another lease."""


class ResourceLimit(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeLimits:
    reserve_bytes: int = 5 * 1024**3
    volume_limit_bytes: int | None = None
    lease_seconds: int = 600
    retry_base_seconds: int = 30
    retry_max_seconds: int = 3600
    pause_seconds: int = 60
    max_attempts: int = 5

    def __post_init__(self) -> None:
        if self.reserve_bytes < 0 or (
            self.volume_limit_bytes is not None and self.volume_limit_bytes <= 0
        ):
            raise ValueError("Storage limits must be nonnegative, with a positive volume cap")
        if (
            min(
                self.lease_seconds,
                self.retry_base_seconds,
                self.retry_max_seconds,
                self.pause_seconds,
                self.max_attempts,
            )
            <= 0
        ):
            raise ValueError("Queue durations and attempt counts must be positive")


class JobQueue:
    def __init__(
        self,
        store: Store,
        *,
        runtime: RuntimeLimits | None = None,
        clock: Callable[[], datetime] = utc_now,
        free_bytes: Callable[[Path], int] | None = None,
    ) -> None:
        self.store = store
        self.runtime = runtime or RuntimeLimits()
        self.clock = clock
        self.free_bytes = free_bytes or (lambda path: shutil.disk_usage(path).free)
        self._lock_fd: int | None = None
        self._lease_token: str | None = None

    def __enter__(self) -> JobQueue:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None
        self._lease_token = None

    def _acquire_lock(self) -> bool:
        if self._lock_fd is not None:
            return False
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.store.data_dir / ".worker.lock", flags, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return False
        self._lock_fd = descriptor
        return True

    def enqueue(
        self,
        scene: Any,
        zone_id: str,
        recipe_version: str,
        *,
        port_id: str | None = None,
        estimated_bytes: int = 0,
        quota_key: str | None = None,
        quota_units: float = 0,
    ) -> str:
        if not zone_id or not recipe_version:
            raise ValueError("Zone and recipe version are required")
        if estimated_bytes < 0 or quota_units < 0 or (quota_units and not quota_key):
            raise ValueError("Resource estimates must be nonnegative and quotas need a key")
        scene_data = as_dict(scene)
        timestamp = utc_text(self.clock())
        with self.store.transaction() as connection:
            key = self.store.register_scene(scene_data, connection=connection)
            scene_data["captured_at"] = connection.execute(
                "SELECT captured_at FROM acquisitions WHERE acquisition_key=?", (key,)
            ).fetchone()[0]
            prior = connection.execute(
                """SELECT jobs.job_id FROM jobs JOIN observations USING(observation_id)
                WHERE acquisition_key=? AND zone_id=? AND recipe_version=?""",
                (key, zone_id, recipe_version),
            ).fetchone()
            if prior:
                return prior["job_id"]
            observation_id = str(uuid.uuid4())
            ports = scene_data.get("port_ids") or []
            if port_id is None and len(ports) == 1:
                port_id = ports[0]
            connection.execute(
                """INSERT INTO observations(observation_id, acquisition_key, zone_id, recipe_version,
                port_id, sensor, captured_at) VALUES (?,?,?,?,?,?,?)""",
                (
                    observation_id,
                    key,
                    zone_id,
                    recipe_version,
                    port_id,
                    scene_data["sensor"],
                    utc_text(scene_data["captured_at"]),
                ),
            )
            job_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO jobs(job_id,observation_id,max_attempts,queued_at,not_before,
                provider,estimated_bytes,quota_key,quota_units,data_json) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id,
                    observation_id,
                    self.runtime.max_attempts,
                    timestamp,
                    timestamp,
                    scene_data.get("provider", ""),
                    estimated_bytes,
                    quota_key,
                    quota_units,
                    json_text({"input_scene": scene_data}),
                ),
            )
        return job_id

    def count_jobs(self) -> int:
        with closing(self.store.connect()) as connection:
            return connection.execute("SELECT count(*) FROM jobs").fetchone()[0]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with closing(self.store.connect()) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def _recover(self, connection: sqlite3.Connection, now: str) -> int:
        expired = connection.execute(
            "SELECT job_id, observation_id FROM jobs WHERE status='running' AND lease_expires_at <= ?",
            (now,),
        ).fetchall()
        for row in expired:
            connection.execute(
                """UPDATE jobs SET status='retry', not_before=?, worker_id=NULL, lease_token=NULL,
                lease_expires_at=NULL, error_code='interrupted', error_message='Worker lease expired after interruption'
                WHERE job_id=?""",
                (now, row["job_id"]),
            )
            connection.execute(
                "UPDATE observations SET status='pending', detection_count=NULL WHERE observation_id=?",
                (row["observation_id"],),
            )
        return len(expired)

    def recover_expired(self) -> int:
        if not self._acquire_lock():
            return 0
        try:
            with self.store.transaction() as connection:
                return self._recover(connection, utc_text(self.clock()))
        finally:
            self.close()

    def _storage_reason(self, required_bytes: int) -> str | None:
        if self.free_bytes(self.store.data_dir) < self.runtime.reserve_bytes + required_bytes:
            return "insufficient_disk"
        if self.runtime.volume_limit_bytes is not None:
            size = sum(
                path.stat().st_size
                for path in self.store.data_dir.rglob("*")
                if path.is_file() and not path.is_symlink()
            )
            if size + required_bytes > self.runtime.volume_limit_bytes:
                return "volume_limit"
        return None

    def _quota_reason(
        self, connection: sqlite3.Connection, job: Mapping[str, Any], now: datetime
    ) -> str | None:
        if not job["quota_key"]:
            return None
        quota = connection.execute(
            "SELECT * FROM quotas WHERE quota_key=?", (job["quota_key"],)
        ).fetchone()
        if quota is None:
            return "quota_unconfigured"
        if quota["period"] != now.strftime("%Y-%m"):
            return "quota_period_expired"
        if quota["used_units"] + job["quota_units"] > quota["limit_units"]:
            return "quota_exhausted"
        return None

    def claim(
        self, worker_id: str, *, recipe_version: str | None = None,
        acquisition_key: str | None = None,
    ) -> dict[str, Any] | None:
        if not worker_id:
            raise ValueError("Worker ID is required")
        if not self._acquire_lock():
            return None
        now = self.clock()
        stamp = utc_text(now)
        claimed: dict[str, Any] | None = None
        try:
            with self.store.transaction() as connection:
                self._recover(connection, stamp)
                if connection.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone():
                    return None
                candidates = connection.execute(
                    """SELECT jobs.* FROM jobs JOIN observations USING(observation_id)
                    WHERE jobs.status IN ('pending','retry','paused') AND not_before <= ?
                    AND (? IS NULL OR observations.recipe_version=?)
                    AND (? IS NULL OR observations.acquisition_key=?) ORDER BY queued_at, job_id""",
                    (stamp, recipe_version, recipe_version, acquisition_key, acquisition_key),
                ).fetchall()
                for row in candidates:
                    reason = self._storage_reason(row["estimated_bytes"]) or self._quota_reason(
                        connection, row, now
                    )
                    if reason:
                        connection.execute(
                            "UPDATE jobs SET status='paused',error_code=?,error_message=?,not_before=? WHERE job_id=?",
                            (
                                reason,
                                reason.replace("_", " "),
                                utc_text(now + timedelta(seconds=self.runtime.pause_seconds)),
                                row["job_id"],
                            ),
                        )
                        continue
                    token = str(uuid.uuid4())
                    connection.execute(
                        """UPDATE jobs SET status='running',attempts=attempts+1,worker_id=?,lease_token=?,
                        lease_expires_at=?,started_at=?,finished_at=NULL,error_code=NULL,error_message=NULL WHERE job_id=?""",
                        (
                            worker_id,
                            token,
                            utc_text(now + timedelta(seconds=self.runtime.lease_seconds)),
                            stamp,
                            row["job_id"],
                        ),
                    )
                    connection.execute(
                        "UPDATE observations SET status='processing' WHERE observation_id=?",
                        (row["observation_id"],),
                    )
                    if row["quota_key"]:
                        connection.execute(
                            "UPDATE quotas SET used_units=used_units+? WHERE quota_key=?",
                            (row["quota_units"], row["quota_key"]),
                        )
                    claimed = dict(
                        connection.execute(
                            "SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)
                        ).fetchone()
                    )
                    self._lease_token = token
                    break
            return claimed
        finally:
            if claimed is None:
                self.close()

    def _assert_lease(self, connection: sqlite3.Connection, job_id: str, token: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if (
            self._lock_fd is None
            or self._lease_token != token
            or row is None
            or row["status"] != "running"
            or row["lease_token"] != token
            or row["lease_expires_at"] <= utc_text(self.clock())
        ):
            raise LeaseLost("The active worker lease is required to modify this job")
        return row

    def heartbeat(self, job_id: str, lease_token: str) -> None:
        with self.store.transaction() as connection:
            self._assert_lease(connection, job_id, lease_token)
            connection.execute(
                "UPDATE jobs SET lease_expires_at=? WHERE job_id=?",
                (utc_text(self.clock() + timedelta(seconds=self.runtime.lease_seconds)), job_id),
            )

    def resume(self, job_id: str) -> None:
        with self.store.transaction() as connection:
            changed = connection.execute(
                "UPDATE jobs SET status='pending',not_before=?,error_code=NULL,error_message=NULL WHERE job_id=? AND status='paused'",
                (utc_text(self.clock()), job_id),
            ).rowcount
            if not changed:
                raise ValueError("Only a paused job can be resumed explicitly")

    def pause(self, job_id: str, lease_token: str, reason: str) -> None:
        """Resource exhaustion is resumable after capacity is restored."""
        with self.store.transaction() as connection:
            job = self._assert_lease(connection, job_id, lease_token)
            connection.execute(
                "UPDATE jobs SET status='paused', error_code=?, error_message=NULL, lease_token=NULL, lease_expires_at=NULL, worker_id=NULL, attempts=max(0,attempts-1) WHERE job_id=?",
                (reason, job_id),
            )
            connection.execute(
                "UPDATE observations SET status='pending', detection_count=NULL WHERE observation_id=?",
                (job["observation_id"],),
            )
        self.close()

    def fail(
        self,
        job_id: str,
        lease_token: str,
        reason: str,
        *,
        transient: bool = True,
        error_code: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        with self.store.transaction() as connection:
            job = self._assert_lease(connection, job_id, lease_token)
            retry = transient and job["attempts"] < job["max_attempts"]
            status = "retry" if retry else ("failed" if transient else "input_failed")
            delay = min(
                self.runtime.retry_base_seconds * (2 ** (job["attempts"] - 1)),
                self.runtime.retry_max_seconds,
            )
            if retry_after_seconds is not None:
                delay = max(delay, retry_after_seconds)
            connection.execute(
                """UPDATE jobs SET status=?,not_before=?,finished_at=?,error_code=?,error_message=?,
                lease_token=NULL,lease_expires_at=NULL,worker_id=NULL WHERE job_id=?""",
                (
                    status,
                    utc_text(self.clock() + timedelta(seconds=delay)),
                    None if retry else utc_text(self.clock()),
                    error_code or ("transient" if transient else "invalid_input"),
                    reason,
                    job_id,
                ),
            )
            connection.execute(
                "UPDATE observations SET status=?,detection_count=NULL WHERE observation_id=?",
                ("pending" if retry else "failed", job["observation_id"]),
            )
        self.close()

    def complete(
        self,
        job_id: str,
        lease_token: str,
        *,
        status: str = "usable",
        observable_fraction: float | None = None,
        quality_flags: Iterable[str] = (),
        detections: Iterable[Any] = (),
        changes: Iterable[Any] = (),
        model_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        predictions = [as_dict(item) for item in detections]
        events = [as_dict(item) for item in changes]
        if status not in {"usable", "partial", "unusable"}:
            raise ValueError("Completed observations must be usable, partial or unusable")
        if status == "unusable" and predictions:
            raise ValueError("An unobservable scene cannot publish detections")
        if observable_fraction is not None and not 0 <= observable_fraction <= 1:
            raise ValueError("Observable fraction must be between zero and one")
        timestamp = utc_text(self.clock())
        with self.store.transaction() as connection:
            job = self._assert_lease(connection, job_id, lease_token)
            observation_id = job["observation_id"]
            for item in predictions:
                detection_id = (
                    item.get("detection_id") or item.get("prediction_id") or str(uuid.uuid4())
                )
                connection.execute(
                    """INSERT INTO detections(detection_id,observation_id,model_id,geometry_json,detection_score,
                    family,length_m,width_m,quality_json,evidence_json,review_status,created_at,data_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        detection_id,
                        observation_id,
                        item.get("model_id", model_id),
                        json_text(item["geometry"]),
                        item.get("detection_score"),
                        item.get("family"),
                        item.get("length_m"),
                        item.get("width_m"),
                        json_text(item.get("quality", item.get("field_quality", {}))),
                        json_text(item.get("evidence_asset_ids", item.get("evidence", []))),
                        item.get("review_status", "provisional"),
                        timestamp,
                        json_text(item),
                    ),
                )
            for item in events:
                connection.execute(
                    "INSERT INTO changes(change_id,observation_id,geometry_json,status,review_status,created_at,data_json) VALUES (?,?,?,?,?,?,?)",
                    (
                        item.get("change_id") or item.get("event_id") or str(uuid.uuid4()),
                        observation_id,
                        json_text(item["geometry"]),
                        item.get("status", "candidate"),
                        item.get("review_status", "provisional"),
                        timestamp,
                        json_text(item),
                    ),
                )
            connection.execute(
                """UPDATE observations SET status=?,observable_fraction=?,quality_flags_json=?,detection_count=?,
                processed_at=?,model_id=?,metadata_json=? WHERE observation_id=?""",
                (
                    status,
                    observable_fraction,
                    json_text(list(quality_flags)),
                    len(predictions) if status != "unusable" else None,
                    timestamp,
                    model_id,
                    json_text(dict(metadata or {})),
                    observation_id,
                ),
            )
            connection.execute(
                """UPDATE jobs SET status='succeeded',finished_at=?,lease_token=NULL,lease_expires_at=NULL,
                worker_id=NULL,error_code=NULL,error_message=NULL WHERE job_id=?""",
                (timestamp, job_id),
            )
        self.close()
        return observation_id

    def publish_asset(
        self,
        job_id: str,
        lease_token: str,
        source: Path | bytes | Iterable[bytes],
        path: str | Path,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        """Publish complete bytes without exposing partial files or replacing evidence."""
        path = relative_path(path)
        destination = confined_path(self.store.data_dir, path, must_exist=False)
        with self.store.transaction() as connection:
            self._assert_lease(connection, job_id, lease_token)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        published = False
        try:
            descriptor, temp_name = tempfile.mkstemp(prefix=".pending-", dir=destination.parent)
            temporary = Path(temp_name)
            with os.fdopen(descriptor, "wb") as output:
                if isinstance(source, Path):
                    with source.open("rb") as input_file:
                        self._write_chunks(output, iter(lambda: input_file.read(1024 * 1024), b""))
                else:
                    self._write_chunks(output, [source] if isinstance(source, bytes) else source)
                output.flush()
                os.fsync(output.fileno())
            with self.store.transaction() as connection:
                job = self._assert_lease(connection, job_id, lease_token)
                if destination.exists():
                    prior = connection.execute(
                        "SELECT * FROM assets WHERE path=?", (path,)
                    ).fetchone()
                    digest = sha256_file(temporary)
                    if (
                        not destination.is_file()
                        or destination.is_symlink()
                        or sha256_file(destination) != digest
                        or (
                            prior
                            and (
                                prior["sha256"] != digest
                                or prior["observation_id"] != job["observation_id"]
                            )
                        )
                    ):
                        raise FileExistsError(destination)
                    if prior:
                        return prior["asset_id"]
                    # A previous process may have exited between link and database commit.
                    return self.store.register_asset(
                        path,
                        observation_id=job["observation_id"],
                        metadata=metadata,
                        connection=connection,
                    )
                # link is an atomic no-replace operation, unlike rename on POSIX.
                os.link(temporary, destination)
                published = True
                directory_fd = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                asset_id = self.store.register_asset(
                    path,
                    observation_id=job["observation_id"],
                    metadata=metadata,
                    connection=connection,
                )
            return asset_id
        except BaseException:
            if published:
                destination.unlink(missing_ok=True)
            raise
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _write_chunks(self, output: Any, chunks: Iterable[bytes]) -> None:
        for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise TypeError("Asset streams must yield bytes")
            reason = self._storage_reason(len(chunk))
            if reason:
                raise ResourceLimit(reason)
            output.write(chunk)

    def enqueue_many(
        self, scenes: Iterable[Any], zones: Mapping[str, Iterable[str]], recipe_version: str
    ) -> list[str]:
        """Idempotent incremental backfill; callers enforce the measured pilot gate."""
        identifiers = []
        for scene in scenes:
            value = as_dict(scene)
            for port_id in value.get("port_ids", []):
                for zone_id in zones.get(port_id, []):
                    identifiers.append(
                        self.enqueue(scene, zone_id, recipe_version, port_id=port_id)
                    )
        return identifiers

    def exceptions(self) -> list[dict[str, Any]]:
        with closing(self.store.connect()) as connection:
            rows = connection.execute(
                """SELECT jobs.job_id,observations.acquisition_key,observations.zone_id,observations.recipe_version,
                jobs.status,jobs.error_code,jobs.error_message,jobs.not_before FROM jobs
                JOIN observations USING(observation_id) WHERE jobs.status != 'succeeded' ORDER BY queued_at,job_id"""
            ).fetchall()
        return [dict(row) for row in rows]
