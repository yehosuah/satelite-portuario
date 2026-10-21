"""Read-only HTTP service with authenticated, audited local annotation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from puertogt.contracts import as_utc
from puertogt.review import ReviewConflict, add_detection, record_review
from puertogt.store import Store, confined_path, utc_text


def config_path() -> Path:
    configured = os.getenv("PUERTOGT_CONFIG_DIR")
    if configured:
        return Path(configured)
    bundled = Path(__file__).parent / "config"
    return bundled if bundled.exists() else Path(__file__).resolve().parents[2] / "config"


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_type: Literal["observation", "detection", "change"]
    entity_id: str = Field(min_length=1, max_length=200)
    correction: dict
    author: str = Field(min_length=1, max_length=120)
    previous_revision_id: str | None = None


class AdditionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_id: str
    geometry: dict
    author: str = Field(min_length=1, max_length=120)


def create_app(
    store: Store | None = None, *, review_token: str | None = None, ports_path: Path | None = None
) -> FastAPI:
    if store is None:
        data_dir = Path(os.getenv("PUERTOGT_DATA_DIR", "data")).expanduser()
        store = Store(data_dir / "catalog.sqlite", data_dir)
    review_token = review_token if review_token is not None else os.getenv("PUERTOGT_REVIEW_TOKEN")
    ports_path = ports_path or config_path() / "ports.geojson"
    app = FastAPI(
        title="Satélite Portuario",
        version="0.1.0",
        description="Observaciones por adquisición. Ningún conteo implica llegadas, TEU ni ausencia absoluta de buques.",
    )
    app.state.store = store

    def ports():
        return (
            json.loads(ports_path.read_text())
            if ports_path.is_file()
            else {"type": "FeatureCollection", "features": []}
        )

    def authorize(request: Request):
        if not review_token:
            raise HTTPException(503, "Review is disabled: configure PUERTOGT_REVIEW_TOKEN")
        authorization = request.headers.get("authorization", "")
        if not hmac.compare_digest(authorization, f"Bearer {review_token}"):
            raise HTTPException(
                401, "Invalid review credential", headers={"WWW-Authenticate": "Bearer"}
            )
        # Browsers must use this origin. CLI clients without Origin can use the bearer token.
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Cross-origin review is not allowed")

    def filters(port_id, sensor, start, end, recipe_version, include_rejected, prefix="o"):
        where, values = [], []
        for key, value in (
            ("port_id", port_id),
            ("sensor", sensor),
            ("recipe_version", recipe_version),
        ):
            if value is not None:
                where.append(f"{prefix}.{key}=?")
                values.append(value)
        try:
            start = as_utc(start) if start else None
            end = as_utc(end) if end else None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if start and end and start >= end:
            raise HTTPException(422, "start must precede end")
        if start:
            where.append(f"{prefix}.captured_at>=?")
            values.append(utc_text(start))
        if end:
            where.append(f"{prefix}.captured_at<?")
            values.append(utc_text(end))
        if not include_rejected:
            where.append(f"{prefix}.review_status!='rejected'")
        return where, values

    def paginate(where, values, cursor, limit, order, identity, table, select="*", extra=()):
        fingerprint = hashlib.sha256(
            json.dumps([table, where, values, extra], sort_keys=True).encode()
        ).hexdigest()[:20]
        where, values = list(where), list(values)
        if cursor:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                if not isinstance(decoded, list) or len(decoded) != 3 or decoded[0] != fingerprint:
                    raise ValueError("cursor does not match filters")
                if not all(isinstance(v, str) for v in decoded):
                    raise ValueError("invalid cursor")
                where.append(f"({order},{identity})>(?,?)")
                values.extend(decoded[1:])
            except (ValueError, TypeError, UnicodeDecodeError) as exc:
                raise HTTPException(422, "Invalid cursor or changed filters") from exc
        condition = " AND ".join(where) or "1=1"
        with closing(store.connect()) as tx:
            rows = [
                dict(r)
                for r in tx.execute(
                    f"SELECT {select} FROM {table} WHERE {condition} ORDER BY {order},{identity} LIMIT ?",
                    (*values, limit + 1),
                )
            ]
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            last = rows[-1]
            payload = [
                fingerprint,
                last[order.rsplit(".", 1)[-1]],
                last[identity.rsplit(".", 1)[-1]],
            ]
            next_cursor = (
                base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
            )
        return rows, next_cursor

    def decode_record(row):
        result = {}
        for key, value in row.items():
            if key.endswith("_json"):
                result[key[:-5]] = json.loads(value) if value is not None else None
            else:
                result[key] = value
        details = result.pop("data", {}) or {}
        return {**details, **result}

    def link_assets(observation):
        with closing(store.connect()) as tx:
            assets = tx.execute(
                "SELECT asset_id,mime_type,crs,bounds_json,attribution,metadata_json FROM assets WHERE observation_id=?",
                (observation["observation_id"],),
            ).fetchall()
            reviewed_count = tx.execute(
                "SELECT count(*) FROM detections WHERE observation_id=? AND review_status!='rejected'",
                (observation["observation_id"],),
            ).fetchone()[0]
        observation["assets"] = [
            {**decode_record(dict(a)), "href": f"/v1/assets/{a['asset_id']}"} for a in assets
        ]
        observation["visible_detection_count"] = (
            reviewed_count if observation["status"] in {"usable", "partial"} else None
        )
        return observation

    @app.get("/health")
    def health():
        with closing(store.connect()) as tx:
            tx.execute("SELECT 1").fetchone()
        return {"status": "ok", "schema_version": 1, "inference_in_http": False}

    @app.get("/v1/ports")
    def get_ports():
        return ports()

    @app.get("/v1/observations")
    def observations(
        port_id: str | None = None,
        sensor: Literal["sentinel-1", "sentinel-2"] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        status: Literal["pending", "processing", "usable", "partial", "unusable", "failed"]
        | None = None,
        recipe_version: str | None = None,
        include_rejected: bool = False,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        cursor: str | None = None,
    ):
        where, values = filters(port_id, sensor, start, end, recipe_version, include_rejected)
        if status:
            where.append("o.status=?")
            values.append(status)
        rows, next_cursor = paginate(
            where,
            values,
            cursor,
            limit,
            "o.captured_at",
            "o.observation_id",
            "observations o",
            "o.*",
        )
        return {
            "items": [link_assets(Store.decode_observation(row)) for row in rows],
            "next_cursor": next_cursor,
        }

    @app.get("/v1/observations/{observation_id}")
    def observation(observation_id: str):
        row = store.get_observation(observation_id)
        if row is None:
            raise HTTPException(404, "Observation not found")
        return link_assets(row)

    @app.get("/v1/detections")
    def detections(
        observation_id: str | None = None,
        port_id: str | None = None,
        sensor: Literal["sentinel-1", "sentinel-2"] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        recipe_version: str | None = None,
        include_rejected: bool = False,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        cursor: str | None = None,
    ):
        where, values = filters(port_id, sensor, start, end, recipe_version, include_rejected)
        if not include_rejected:
            where.append("d.review_status!='rejected'")
        where.append("o.status IN ('usable','partial')")
        if observation_id:
            where.append("d.observation_id=?")
            values.append(observation_id)
        rows, next_cursor = paginate(
            where,
            values,
            cursor,
            limit,
            "o.captured_at",
            "d.detection_id",
            "detections d JOIN observations o ON o.observation_id=d.observation_id",
            "d.*,o.captured_at,o.port_id,o.sensor,o.recipe_version",
        )
        features = []
        for row in rows:
            properties = decode_record(row)
            geometry = properties.pop("geometry")
            properties["evidence_links"] = [
                f"/v1/assets/{asset}" for asset in properties.get("evidence", [])
            ]
            features.append(
                {
                    "type": "Feature",
                    "id": row["detection_id"],
                    "geometry": geometry,
                    "properties": properties,
                }
            )
        return {"type": "FeatureCollection", "features": features, "next_cursor": next_cursor}

    @app.get("/v1/changes")
    def changes(
        port_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        include_rejected: bool = False,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        cursor: str | None = None,
    ):
        where, values = filters(port_id, None, start, end, None, include_rejected)
        if not include_rejected:
            where.extend(["c.review_status!='rejected'", "c.status!='rejected'"])
        rows, next_cursor = paginate(
            where,
            values,
            cursor,
            limit,
            "o.captured_at",
            "c.change_id",
            "changes c JOIN observations o ON o.observation_id=c.observation_id",
            "c.*,o.captured_at,o.port_id,o.sensor",
        )
        return {"items": [decode_record(row) for row in rows], "next_cursor": next_cursor}

    @app.get("/v1/jobs")
    def jobs(limit: Annotated[int, Query(ge=1, le=500)] = 100, cursor: str | None = None):
        columns = "job_id,observation_id,status,attempts,max_attempts,queued_at,not_before,started_at,finished_at,error_code"
        rows, next_cursor = paginate([], [], cursor, limit, "queued_at", "job_id", "jobs", columns)
        return {"items": rows, "next_cursor": next_cursor}

    @app.get("/v1/models")
    def models():
        with closing(store.connect()) as tx:
            rows = tx.execute("SELECT data_json FROM models ORDER BY model_id").fetchall()
        return {"items": [json.loads(r[0]) for r in rows]}

    @app.get("/v1/assets/{asset_id}")
    def asset(asset_id: str, metadata: bool = False):
        with closing(store.connect()) as tx:
            row = tx.execute(
                "SELECT * FROM assets WHERE asset_id=? AND state='ready'", (asset_id,)
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Asset not found")
        try:
            path = confined_path(store.data_dir, row["path"])
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, "Registered asset unavailable") from exc
        if metadata:
            info = decode_record(dict(row))
            info.pop("path", None)
            return {**info, "href": f"/v1/assets/{asset_id}"}
        return FileResponse(
            path,
            media_type=row["mime_type"],
            headers={
                "ETag": f'"{row["sha256"]}"',
                "X-Content-Type-Options": "nosniff",
                "X-Asset-Id": asset_id,
            },
        )

    @app.get("/v1/coverage")
    def coverage(port_id: str | None = None, recipe_version: str | None = None):
        from puertogt.coverage import coverage_report

        report = coverage_report(store, ports_path, port_id=port_id, recipe_version=recipe_version)
        report.pop("exceptions")
        return report

    @app.post("/v1/reviews", dependencies=[Depends(authorize)])
    def review(body: ReviewInput):
        try:
            revision_id = record_review(
                store,
                body.entity_id,
                body.correction,
                body.author,
                entity_type=body.entity_type,
                previous_revision_id=body.previous_revision_id,
            )
            return {"revision_id": revision_id}
        except ReviewConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "Entity not found") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/reviews/detections", dependencies=[Depends(authorize)])
    def addition(body: AdditionInput):
        try:
            return {
                "detection_id": add_detection(
                    store, body.observation_id, body.geometry, body.author
                )
            }
        except KeyError as exc:
            raise HTTPException(404, "Observation not found") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/v1/reviews/{entity_type}/{entity_id}")
    def audit(entity_type: str, entity_id: str):
        with closing(store.connect()) as tx:
            rows = tx.execute(
                "SELECT * FROM revisions WHERE entity_type=? AND entity_id=? ORDER BY rowid",
                (entity_type, entity_id),
            ).fetchall()
        return {"items": [decode_record(dict(r)) for r in rows]}

    web = Path(__file__).parent / "web"
    if web.is_dir():
        app.mount("/", StaticFiles(directory=web, html=True), name="review-ui")
    return app
