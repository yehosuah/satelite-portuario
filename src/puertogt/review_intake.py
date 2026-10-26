"""Immutable first inspections, separate from training labels and model proposals."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from uuid import UUID

from pyproj import Transformer
from shapely.geometry import box, mapping, shape
from shapely.ops import transform

from puertogt.review import ReviewConflict, validate_geometry
from puertogt.review_queue import blind_asset, blind_entry
from puertogt.store import Store, json_text, sha256_file, utc_text


def preview_projection(entry: dict, asset: dict):
    """Map the preview's native grid rather than linearly stretching WGS84 bounds."""
    west, south, east, north = asset["bounds"]
    if not all(math.isfinite(n) for n in (west, south, east, north)) or west >= east or south >= north:
        raise ValueError("Invalid preview bounds")
    forward = Transformer.from_crs("EPSG:4326", asset["crs"], always_xy=True)
    reverse = Transformer.from_crs(asset["crs"], "EPSG:4326", always_xy=True)

    def to_pixel(x, y, z=None):
        x, y = forward.transform(x, y)
        return ((x - west) / (east - west), (north - y) / (north - south))

    def to_world(x, y, z=None):
        return reverse.transform(west + x * (east - west), north - y * (north - south))

    return to_pixel, to_world


def projected_zone(entry: dict, asset: dict) -> dict:
    to_pixel, _ = preview_projection(entry, asset)
    return mapping(transform(to_pixel, shape(entry["zone_geometry"])))


def preview_rectangle(store: Store, manifest: dict, submission: dict) -> dict:
    if submission["queue_id"] != manifest["queue_id"]:
        raise ReviewConflict("The active review queue changed; reload it")
    entry = blind_entry(manifest, submission["observation_id"])
    asset = next((a for a in entry["assets"] if a["asset_id"] == submission["asset_id"]), None)
    if asset is None:
        raise KeyError(submission["asset_id"])
    blind_asset(store, manifest, entry["observation_id"], asset["asset_id"])
    left, top, right, bottom = submission["rectangle"]
    if (not all(math.isfinite(n) and 0 <= n <= 1 for n in (left, top, right, bottom))
            or left >= right or top >= bottom):
        raise ValueError("Rectangle must have positive area within the preview")
    to_pixel, to_world = preview_projection(entry, asset)
    geometry = transform(to_world, box(left, top, right, bottom))
    if submission["kind"] == "area":
        geometry = geometry.intersection(shape(entry["zone_geometry"]))
        if geometry.is_empty or geometry.area <= 0 or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError("Rectangle must overlap an area of the zone")
    return {"geometry": mapping(geometry), "pixel_geometry": mapping(transform(to_pixel, geometry))}


def record_first_pass(store: Store, manifest: dict, submission: dict) -> dict:
    """Freeze raw-image findings without changing observations or certifying labels."""
    if submission["queue_id"] != manifest["queue_id"]:
        raise ReviewConflict("The active review queue changed; reload it")
    if not re.fullmatch(r"train-[a-f0-9]{20}", manifest["queue_id"]):
        raise ValueError("Invalid queue identifier")
    identity = str(UUID(submission["inspection_id"]))
    if identity != submission["inspection_id"]:
        raise ValueError("inspection_id must be a canonical UUID")
    entry = blind_entry(manifest, submission["observation_id"])
    for asset in entry["assets"]:
        blind_asset(store, manifest, entry["observation_id"], asset["asset_id"])
    if not submission["author"].strip():
        raise ValueError("author is required")
    if submission["reviewer_kind"] not in {"human_visual_review", "ai_visual_review"}:
        raise ValueError("reviewer_kind is required")
    decision = submission["decision"]
    candidates = submission["candidates"]
    area = submission.get("reviewed_area")
    if decision not in {"candidates", "empty", "uncertain"}:
        raise ValueError("invalid first-pass decision")
    if decision == "candidates" and not candidates:
        raise ValueError("Mark at least one candidate")
    if decision == "empty" and candidates:
        raise ValueError("An empty area cannot include candidates")
    if decision == "uncertain" and not submission.get("note", "").strip():
        raise ValueError("Describe why the image is uncertain")
    if decision != "uncertain" and area is None:
        raise ValueError("Delimit the inspected area before recording findings")
    zone = shape(entry["zone_geometry"])
    if area is not None:
        validate_geometry(area)
        scope = shape(area)
        if scope.geom_type not in {"Polygon", "MultiPolygon"} or not zone.buffer(1e-10).covers(scope):
            raise ValueError("Inspected area must remain inside the configured zone")
    else:
        scope = zone
    if len(candidates) > 100:
        raise ValueError("Too many first-pass candidates")
    for candidate in candidates:
        validate_geometry(candidate)
        geometry = shape(candidate)
        if geometry.geom_type not in {"Polygon", "MultiPolygon"} or not scope.covers(geometry.centroid):
            raise ValueError("Candidate centers must lie inside the inspected area")
    if not 0 <= submission["elapsed_seconds"] <= 86400:
        raise ValueError("Invalid inspection duration")
    request_hash = hashlib.sha256(json_text(submission).encode()).hexdigest()
    directory = store.data_dir / "review-intake" / manifest["queue_id"]
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{identity}.json"
    if destination.exists():
        existing = json.loads(destination.read_text())
        if existing["submission_sha256"] != request_hash:
            raise ReviewConflict("Inspection identifier already contains different findings")
        return _receipt(existing, destination)
    result = {
        "schema": "puertogt-first-pass-v1", "recorded_at": utc_text(),
        "submission": submission, "submission_sha256": request_hash,
        "queue_id": manifest["queue_id"], "source_entry_sha256": hashlib.sha256(json_text(entry).encode()).hexdigest(),
        "source_assets": [{"asset_id": item["asset_id"], "sha256": item["sha256"]} for item in entry["assets"]],
        "is_training_label": False, "is_independent_validation": False,
        "exposure": "UI first inspection without model proposals; prior exposure outside this workflow is unknown",
        "duration_source": "reviewer_client_elapsed_seconds",
    }
    descriptor, temporary = tempfile.mkstemp(prefix=".inspection-", dir=directory)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(json_text(result) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            existing = json.loads(destination.read_text())
            if existing["submission_sha256"] != request_hash:
                raise ReviewConflict("Inspection identifier already contains different findings") from None
            result = existing
    finally:
        Path(temporary).unlink(missing_ok=True)
    return _receipt(result, destination)


def _receipt(record: dict, path: Path) -> dict:
    submission = record["submission"]
    return {
        "inspection_id": submission["inspection_id"], "queue_id": record["queue_id"],
        "observation_id": submission["observation_id"], "recorded_at": record["recorded_at"],
        "sha256": sha256_file(path), "decision": submission["decision"],
        "is_training_label": False, "is_independent_validation": False,
    }
