"""Audited corrections and immutable datasets; model proposals are never ground truth."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from shapely.geometry import shape

from puertogt.store import Store, confined_path, json_text, sha256_file, utc_text


class ReviewConflict(ValueError):
    """The entity changed since the reviewer loaded it."""


def record_review(
    store: Store,
    entity_id: str,
    correction: dict,
    author: str,
    *,
    entity_type: str = "detection",
    previous_revision_id: str | None = None,
) -> str:
    """Apply one validated correction and retain the original proposal in the same transaction."""
    tables = {
        "detection": ("detections", "detection_id"),
        "observation": ("observations", "observation_id"),
        "change": ("changes", "change_id"),
    }
    if entity_type not in tables or not author.strip() or not correction:
        raise ValueError("entity_type, author and correction are required")
    table, key = tables[entity_type]
    allowed = {"review_status", "note", "provenance"}
    if entity_type == "detection":
        allowed |= {"geometry", "family", "length_m", "width_m"}
    elif entity_type == "observation":
        allowed |= {
            "status",
            "observable_fraction",
            "dataset_split",
            "split_groups",
            "scene_complete",
        }
    else:
        allowed |= {"geometry", "status"}
    if set(correction) - allowed:
        raise ValueError("unsupported correction fields")
    if "review_status" in correction and correction["review_status"] not in {
        "reviewed",
        "rejected",
        "provisional",
    }:
        raise ValueError("invalid review status")
    if "geometry" in correction:
        validate_geometry(correction["geometry"])
    if entity_type == "detection":
        if correction.get("family") not in {
            None,
            "carga",
            "tanquero",
            "pasajeros",
            "remolcador",
            "pesquero",
            "otros",
        }:
            raise ValueError("unknown family; use null to abstain")
        for field in ("family", "length_m", "width_m"):
            value = correction.get(field)
            if value is None:
                continue
            provenance = correction.get("provenance", {}).get(field, {})
            if not provenance.get("reference") or provenance.get("kind") not in {
                "public_dataset",
                "independent_reference",
            }:
                raise ValueError(f"{field} requires independent provenance")
            if field != "family" and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 < value < 1000
            ):
                raise ValueError("dimensions must be positive finite metres")
    if entity_type == "observation":
        if "status" in correction and correction["status"] != "unusable":
            raise ValueError("review can mark unusable; processing states belong to the worker")
        fraction = correction.get("observable_fraction")
        if fraction is not None and not 0 <= fraction <= 1:
            raise ValueError("observable_fraction must be between zero and one")
        if "dataset_split" in correction and correction["dataset_split"] not in {
            "train",
            "calibration",
            "test",
        }:
            raise ValueError("invalid dataset split")
        if "scene_complete" in correction and type(correction["scene_complete"]) is not bool:
            raise ValueError("scene_complete must be boolean")
        if "split_groups" in correction and (
            not isinstance(correction["split_groups"], list)
            or not all(isinstance(v, str) and v for v in correction["split_groups"])
        ):
            raise ValueError("split_groups must contain non-empty identifiers")
    if entity_type == "change" and "status" in correction and correction["status"] != "rejected":
        raise ValueError("persistence and reversion require temporal evidence")
    revision_id = str(uuid.uuid4())
    now = utc_text()
    with store.transaction() as tx:
        row = tx.execute(f"SELECT * FROM {table} WHERE {key}=?", (entity_id,)).fetchone()
        if row is None:
            raise KeyError(entity_id)
        previous = tx.execute(
            "SELECT revision_id FROM revisions WHERE entity_type=? AND entity_id=? ORDER BY rowid DESC LIMIT 1",
            (entity_type, entity_id),
        ).fetchone()
        if (previous[0] if previous else None) != previous_revision_id:
            raise ReviewConflict("reload the entity before applying a correction")
        proposal = dict(row)
        tx.execute(
            "INSERT INTO revisions(revision_id,entity_type,entity_id,previous_revision_id,proposal_json,correction_json,author,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                revision_id,
                entity_type,
                entity_id,
                previous_revision_id,
                json_text(proposal),
                json_text(correction),
                author.strip(),
                now,
            ),
        )
        updates = {"review_status": correction.get("review_status", "reviewed")}
        for name in ("geometry", "family", "length_m", "width_m", "status", "observable_fraction"):
            if name in correction:
                updates["geometry_json" if name == "geometry" else name] = (
                    json_text(correction[name]) if name == "geometry" else correction[name]
                )
        if entity_type == "observation" and correction.get("status") == "unusable":
            updates["detection_count"] = None
        details = json.loads(row["data_json"])
        provenance = {**details.get("provenance", {}), **correction.get("provenance", {})}
        for field in ("family", "length_m", "width_m"):
            if field in correction and correction[field] is None:
                provenance.pop(field, None)
        details.update(
            {
                k: v
                for k, v in correction.items()
                if k in {"note", "provenance", "dataset_split", "split_groups", "scene_complete"}
            }
        )
        if provenance:
            details["provenance"] = provenance
        else:
            details.pop("provenance", None)
        details["latest_revision_id"] = revision_id
        updates["data_json"] = json_text(details)
        tx.execute(
            f"UPDATE {table} SET "
            + ",".join(f"{field}=?" for field in updates)
            + f" WHERE {key}=?",
            (*updates.values(), entity_id),
        )
    return revision_id


def add_detection(store: Store, observation_id: str, geometry: dict, author: str) -> str:
    """Record an omitted object as an audited manual annotation, with no invented model score."""
    validate_geometry(geometry)
    if not author.strip():
        raise ValueError("author is required")
    detection_id, revision_id = str(uuid.uuid4()), str(uuid.uuid4())
    with store.transaction() as tx:
        observation = tx.execute(
            "SELECT * FROM observations WHERE observation_id=?", (observation_id,)
        ).fetchone()
        if observation is None:
            raise KeyError(observation_id)
        if observation["status"] not in {"usable", "partial"}:
            raise ValueError("cannot annotate an unobservable scene")
        tx.execute(
            "INSERT INTO detections(detection_id,observation_id,geometry_json,review_status,created_at,data_json) VALUES (?,?,?,'reviewed',?,?)",
            (
                detection_id,
                observation_id,
                json_text(geometry),
                utc_text(),
                json_text({"origin": "manual", "latest_revision_id": revision_id}),
            ),
        )
        tx.execute(
            "INSERT INTO revisions(revision_id,entity_type,entity_id,proposal_json,correction_json,author,created_at) VALUES (?,?,?,?,?,?,?)",
            (
                revision_id,
                "detection",
                detection_id,
                "null",
                json_text({"action": "add", "geometry": geometry}),
                author.strip(),
                utc_text(),
            ),
        )
    return detection_id


def export_dataset(store: Store, destination: Path, *, split: str, version: str) -> dict[str, Any]:
    """Publish a complete immutable dataset or leave its destination untouched."""
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("dataset versions are immutable")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.pending-", dir=destination.parent))
    try:
        staged_dataset = stage / "dataset"
        manifest = _export_dataset(store, staged_dataset, split=split, version=version)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("dataset versions are immutable")
        staged_dataset.rename(destination)
        return manifest
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _export_dataset(store: Store, destination: Path, *, split: str, version: str) -> dict[str, Any]:
    """Freeze reviewed full-scene labels, checking leakage across all assigned splits."""
    if split not in {"train", "calibration", "test"}:
        raise ValueError("invalid split")
    if destination.exists():
        raise FileExistsError("dataset versions are immutable")
    from puertogt.catalog import DEFAULT_PORTS_PATH

    config = (
        Path(os.getenv("PUERTOGT_CONFIG_DIR", str(DEFAULT_PORTS_PATH.parent))) / "ports.geojson"
    )
    config_bytes = config.read_bytes()
    ports = json.loads(config_bytes)
    config_hash = hashlib.sha256(config_bytes).hexdigest()
    zones = {feature["properties"]["zone_id"]: feature for feature in ports["features"]}
    with closing(store.connect()) as tx:
        tx.execute("BEGIN")
        rows = tx.execute(
            "SELECT * FROM observations ORDER BY captured_at,observation_id"
        ).fetchall()
        memberships: dict[str, str] = {}
        chosen = []
        for row in rows:
            details = json.loads(row["data_json"])
            assigned = details.get("dataset_split")
            if not assigned:
                continue
            groups = [row["acquisition_key"], *details.get("split_groups", [])]
            for group in groups:
                if group in memberships and memberships[group] != assigned:
                    raise ValueError(f"split leakage: related group {group}")
                memberships[group] = assigned
            if (
                assigned != split
                or row["review_status"] != "reviewed"
                or row["status"] not in {"usable", "partial"}
            ):
                continue
            if not details.get("scene_complete"):
                raise ValueError(
                    "complete-scene review is required; accepted proposals alone cannot measure recall"
                )
            if (
                row["zone_id"] not in zones
                or zones[row["zone_id"]]["properties"]["port_id"] != row["port_id"]
            ):
                raise ValueError("Reviewed observation has no matching configured zone")
            if tx.execute(
                "SELECT 1 FROM detections WHERE observation_id=? AND review_status='provisional' LIMIT 1",
                (row["observation_id"],),
            ).fetchone():
                raise ValueError(
                    "resolve all provisional detections before freezing a complete scene"
                )
            detections = tx.execute(
                "SELECT * FROM detections WHERE observation_id=? AND review_status='reviewed'",
                (row["observation_id"],),
            ).fetchall()
            features = [
                {
                    "type": "Feature",
                    "geometry": json.loads(d["geometry_json"]),
                    "properties": {
                        "id": d["detection_id"],
                        "family": d["family"],
                        "length_m": d["length_m"],
                        "width_m": d["width_m"],
                        "provenance": json.loads(d["data_json"]).get("provenance", {}),
                    },
                }
                for d in detections
            ]
            assets = [
                dict(asset)
                for asset in tx.execute(
                    "SELECT asset_id,path,sha256,mime_type,metadata_json FROM assets WHERE observation_id=?",
                    (row["observation_id"],),
                )
            ]
            chosen.append((dict(row), features, groups, assets))
    if not chosen:
        raise ValueError("no reviewed complete scenes in this split")
    destination.mkdir(parents=True)
    manifest = {
        "version": version,
        "split": split,
        "created_at": utc_text(),
        "format": "puertogt-reviewed-geojson-v1",
        "samples": [],
    }
    for row, features, groups, assets in chosen:
        path = destination / f"{row['observation_id']}.geojson"
        path.write_text(json_text({"type": "FeatureCollection", "features": features}) + "\n")
        evidence = []
        for asset in assets:
            source = confined_path(store.data_dir, asset["path"])
            if sha256_file(source) != asset["sha256"]:
                raise ValueError("evidence checksum changed before freezing dataset")
            relative = f"evidence/{asset['asset_id']}{source.suffix}"
            target = confined_path(destination, relative, must_exist=False)
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, target)
            evidence.append(
                {
                    "asset_id": asset["asset_id"],
                    "path": relative,
                    "sha256": asset["sha256"],
                    "mime_type": asset["mime_type"],
                    "metadata": json.loads(asset["metadata_json"]),
                }
            )
        manifest["samples"].append(
            {
                "observation_id": row["observation_id"],
                "acquisition_key": row["acquisition_key"],
                "captured_at": row["captured_at"],
                "port_id": row["port_id"],
                "zone_id": row["zone_id"],
                "sensor": row["sensor"],
                "reviewed_area": zones[row["zone_id"]]["geometry"],
                "reviewed_area_scope": "frozen_area",
                "reviewed_area_source": {
                    "zone_id": row["zone_id"],
                    "port_id": row["port_id"],
                    "ports_config_file": "ports.geojson",
                    "ports_config_sha256": config_hash,
                    "ports_config_version": ports.get("version"),
                    "object_membership": "centroid_covered_by_reviewed_area",
                },
                "reference_provenance": json.loads(row["data_json"]).get("provenance", {}),
                "split_groups": groups,
                "labels": path.name,
                "sha256": sha256_file(path),
                "evidence_assets": evidence,
            }
        )
    (destination / "manifest.json").write_text(json_text(manifest) + "\n")
    return manifest


def validate_geometry(geometry: dict) -> None:
    candidate = shape(geometry)
    if candidate.is_empty or not candidate.is_valid:
        raise ValueError("geometry must be non-empty and valid")
    xmin, ymin, xmax, ymax = candidate.bounds
    if not (-180 <= xmin <= xmax <= 180 and -90 <= ymin <= ymax <= 90):
        raise ValueError("geometry must use WGS84 longitude/latitude")


def bundle_from_evidence(sample: dict, dataset_dir: Path):
    """Rebuild numeric inputs from the immutable evidence copied with reviewed labels."""
    from puertogt.contracts import RasterBundle

    bands, masks, units, indices = {}, {}, {}, {}
    base = None
    crs = None
    for asset in sample.get("evidence_assets", []):
        metadata = asset.get("metadata", {})
        descriptor = metadata.get("bundle")
        if not descriptor:
            continue
        path = confined_path(dataset_dir, asset["path"])
        if sha256_file(path) != asset["sha256"]:
            raise ValueError("numeric evidence checksum mismatch")
        for band in descriptor["bands"]:
            bands[band] = path
        for mask in descriptor["masks"]:
            masks[mask] = path
        units.update(descriptor["units"])
        indices.update(descriptor.get("band_indices", {}))
        base = descriptor
        crs = metadata.get("crs")
    if not bands or base is None or crs is None:
        raise ValueError(
            "dataset has no portable numeric bundle; export observations processed by this worker"
        )
    return RasterBundle(
        band_paths=bands,
        mask_paths=masks,
        units=units,
        crs=crs,
        transform=base["transform"],
        sensor=base["sensor"],
        product_level=base["product_level"],
        preprocessing_version=base["preprocessing_version"],
        observation_id=sample["observation_id"],
        metadata={
            **base.get("metadata", {}),
            "band_indices": indices,
            "captured_at": sample["captured_at"],
        },
    )
