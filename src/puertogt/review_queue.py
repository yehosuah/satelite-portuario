"""Metadata-only TRAIN review queues; immutable proposals, never labeled datasets."""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path
from uuid import UUID

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError
from shapely.geometry import box, shape
from shapely.ops import transform

from puertogt.ml.history_evidence import _pass_id
from puertogt.pass_coverage import pass_key
from puertogt.store import confined_path, json_text, sha256_file, utc_text

STRATA = [(p, s) for p in ("prq", "pbr", "stc") for s in ("sentinel-1", "sentinel-2")]


def valid_preview_georeference(asset):
    """Require a real projection and a finite positive grid before freezing it."""
    try:
        if not asset["crs"]:
            return False
        CRS.from_user_input(asset["crs"])
        bounds = json.loads(asset["native_bounds"] or "null")
        if (not isinstance(bounds, list) or len(bounds) != 4
                or not all(type(v) in (int, float) and math.isfinite(v) for v in bounds)):
            return False
        return bounds[0] < bounds[2] and bounds[1] < bounds[3]
    except (TypeError, ValueError, CRSError):
        return False


def digest(value):
    return hashlib.sha256(json_text(value).encode()).hexdigest()


def _polygon_geometry(value):
    """Require finite, two-dimensional WGS84 polygons before using GEOS."""
    if not isinstance(value, dict) or value.get("type") not in {"Polygon", "MultiPolygon"}:
        raise ValueError("Search scope and reference zone must be polygonal")
    polygons = value.get("coordinates")
    if value["type"] == "Polygon":
        polygons = [polygons]
    if not isinstance(polygons, (list, tuple)) or not polygons:
        raise ValueError("Search scope has no polygon coordinates")
    for polygon in polygons:
        if not isinstance(polygon, (list, tuple)) or not polygon:
            raise ValueError("Search scope has no polygon rings")
        for ring in polygon:
            if not isinstance(ring, (list, tuple)) or len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("Search scope rings must be closed")
            for point in ring:
                if (not isinstance(point, (list, tuple)) or len(point) != 2
                        or not all(type(n) in (int, float) and math.isfinite(n) for n in point)
                        or not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90):
                    raise ValueError("Search scope coordinates must be finite WGS84 positions")
    geometry = shape(value)
    if geometry.is_empty or not geometry.is_valid or geometry.area <= 0:
        raise ValueError("Search scope must have valid positive area")
    return geometry


def validate_search_scope(entry):
    """Check the claimed extent independently of its content hashes.

    The proposal's SHA records provenance; the queue builder remains responsible
    for deriving water geometry from that pinned source, not from detections.
    """
    if "search_scope" not in entry:
        return
    scope = entry["search_scope"]
    fields = {"schema_version", "radius_m", "geometry", "geometry_sha256",
              "reference_zone_sha256", "source_sha256", "frame_clipped", "observability"}
    if (not isinstance(scope, dict) or set(scope) != fields
            or scope["schema_version"] != "water-search-scope-v1"
            or type(scope["radius_m"]) not in (int, float) or scope["radius_m"] != 500
            or type(scope["frame_clipped"]) is not bool or scope["observability"] != "not_measured"):
        raise ValueError("Invalid water search scope contract")
    if (not isinstance(scope["source_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", scope["source_sha256"])
            or scope["geometry_sha256"] != digest(scope["geometry"])
            or scope["reference_zone_sha256"] != digest(entry["zone_geometry"])):
        raise ValueError("Search scope source or geometry checksum mismatch")
    geometry = _polygon_geometry(scope["geometry"])
    reference = _polygon_geometry(entry["zone_geometry"])
    center = reference.centroid
    utm_zone = min(60, max(1, int((center.x + 180) // 6) + 1))
    metric = Transformer.from_crs(4326, (32600 if center.y >= 0 else 32700) + utm_zone, always_xy=True)
    projected_reference = transform(metric.transform, reference)
    projected_scope = transform(metric.transform, geometry)
    # One centimetre allows projection round-off, not a further search margin.
    if (not projected_scope.is_valid or not projected_reference.is_valid
            or not projected_reference.buffer(scope["radius_m"] + .01, quad_segs=64).covers(projected_scope)):
        raise ValueError("Search scope exceeds its declared radius around the reference zone")
    if not entry.get("assets"):
        raise ValueError("Search scope requires georeferenced preview evidence")
    for asset in entry["assets"]:
        bounds = asset.get("bounds")
        if (asset.get("mime_type") != "image/png" or not asset.get("crs")
                or not isinstance(bounds, (list, tuple)) or len(bounds) != 4
                or not all(type(n) in (int, float) and math.isfinite(n) for n in bounds)
                or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]):
            raise ValueError("Search scope requires finite georeferenced preview bounds")
        try:
            projection = Transformer.from_crs(4326, asset["crs"], always_xy=True)
            native = transform(projection.transform, geometry)
        except (CRSError, TypeError, ValueError) as exc:
            raise ValueError("Search scope preview projection is invalid") from exc
        tolerance = max(1.0, *(abs(n) for n in bounds)) * 1e-9
        if not native.is_valid or not box(*bounds).buffer(tolerance).covers(native):
            raise ValueError("Search scope extends outside the available preview")


def effective_search_geometry(entry):
    """Return the declared inspection extent without changing zone attribution."""
    validate_search_scope(entry)
    return entry["search_scope"]["geometry"] if "search_scope" in entry else entry["zone_geometry"]


def _validate_queue_scopes(manifest):
    for entry in manifest["entries"]:
        if "search_scope" in entry and manifest["schema_version"] != "blind-train-queue-v2":
            raise ValueError("Search scopes require a V2 review queue")
        validate_search_scope(entry)


def first_pass_exposure(data_dir):
    """Read receipt identity only; findings never rank or select new scenes.

    The submission is hashed opaquely for integrity. Its decisions, geometries,
    notes and author are not used or exposed in the queue metadata.
    """
    identities = []
    for path in sorted((data_dir / "review-intake").glob("*/*.json")):
        try:
            raw = confined_path(data_dir, path.relative_to(data_dir)).read_bytes()
            record = json.loads(raw)
            submission = record["submission"]
            queue_id, inspection_id = submission["queue_id"], submission["inspection_id"]
            observation_id = submission["observation_id"]
            if (record["schema"] != "puertogt-first-pass-v1"
                    or record["queue_id"] != queue_id
                    or not re.fullmatch(r"train-[a-f0-9]{20}", queue_id)
                    or path.parent.name != queue_id or path.stem != inspection_id
                    or str(UUID(inspection_id)) != inspection_id
                    or not isinstance(observation_id, str) or not observation_id
                    or record["submission_sha256"] != digest(submission)):
                raise ValueError("Invalid receipt identity or checksum")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"Invalid first-pass receipt: {path}") from exc
        identities.append({"observation_id": observation_id, "queue_id": queue_id,
                           "inspection_id": inspection_id, "sha256": hashlib.sha256(raw).hexdigest()})
    return identities


def groups(key):
    return {key, _pass_id(key), physical_pass(key)}


def physical_pass(key):
    """Conservatively count S1 datatakes in one absolute orbit as one visit."""
    return pass_key(key, "sentinel-1" if key.startswith("S1") else "sentinel-2")


def linked_groups(values):
    result = set(values)
    for value in values:
        try:
            result.update(groups(value))
        except ValueError:
            pass  # Custom split groups remain exact opaque identifiers.
    return result


def build_review_queue(store, sampling_path: Path, order_path: Path, ports_path: Path,
                       *, per_stratum=2, minimum_observable=.5):
    """Select at most one marine zone per port/pass, with balanced quotas.

    Only existing TRAIN primaries and explicitly activated reserves are eligible.
    Existing observation review contents are never loaded. No detector table or
    model counts are queried. Missing strata stay deficient.
    """
    if type(per_stratum) is not int or not 1 <= per_stratum <= 20 or not 0 <= minimum_observable <= 1:
        raise ValueError("Use 1..20 passes per stratum and an observable fraction in 0..1")
    sampling, order, ports = [json.loads(p.read_text()) for p in (sampling_path, order_path, ports_path)]
    window = sampling["windows"]["train"]
    if window != {"start": "2025-01-01", "end_exclusive": "2025-07-01", "anchor_days": [1, 16]}:
        raise ValueError("This policy is fixed to the original January-June 2025 TRAIN window")
    start, end = [utc_text(window[k] + "T00:00:00Z") for k in ("start", "end_exclusive")]
    gap = (datetime.fromisoformat(sampling["windows"]["calibration"]["start"])
           - datetime.fromisoformat(window["end_exclusive"])).days
    if gap < 92:
        raise ValueError("The original temporal embargo must be preserved")
    cal_end = datetime.fromisoformat(sampling["windows"]["calibration"]["end_exclusive"])
    test_start = datetime.fromisoformat(sampling["windows"]["test"]["start"])
    if (test_start - cal_end).days < 91:
        raise ValueError("The calibration-to-TEST temporal embargo must be preserved")
    active = {(r["split"], p, r["sensor"], r["reserve_acquisition_key"])
              for r in order["activated_reserves"] for p in r["port_ids"]}
    allowed, reserved = {}, set()
    for slot in sampling["slots"]:
        for candidate in ([slot["selected"]] if slot["selected"] else []) + slot["reserves"]:
            key = candidate["acquisition_key"]
            context = (candidate.get("context") or {}).get("acquisition_key")
            if slot["sensor"] == "sentinel-1" and not context:
                raise ValueError("Every radar candidate must preserve its planned context")
            if context:
                target_time = datetime.fromisoformat(utc_text(candidate["captured_at"]))
                context_time = datetime.fromisoformat(utc_text(candidate["context"]["captured_at"]))
                if not 30 <= (target_time - context_time).total_seconds() / 86400 <= 90:
                    raise ValueError("Radar context must retain the 30..90 day separation")
            linked = groups(key) | (groups(context) if context else set())
            if slot["split"] != "train":
                reserved.update(linked)
            elif candidate == slot["selected"] or ("train", slot["port_id"], slot["sensor"], key) in active:
                allowed[(key, slot["port_id"], slot["sensor"])] = linked
    zones = {f["properties"]["zone_id"]: f for f in ports["features"]}
    receipt_metadata = first_pass_exposure(store.data_dir)
    previously_inspected = {r["observation_id"] for r in receipt_metadata}
    with closing(store.connect()) as tx:
        tx.execute("BEGIN")
        # SQL extracts only assignment/completeness, never notes or geometries.
        assignments = [dict(r) for r in tx.execute("""SELECT acquisition_key,
            json_extract(data_json,'$.dataset_split') AS split,
            json_extract(data_json,'$.split_groups') AS split_groups
            FROM observations WHERE json_extract(data_json,'$.dataset_split') IN ('calibration','test')
            ORDER BY acquisition_key,split,split_groups""")]
        for row in assignments:
            reserved.update(groups(row["acquisition_key"]))
            reserved.update(linked_groups(json.loads(row["split_groups"] or "[]")))
        rows = [dict(r) for r in tx.execute("""SELECT o.observation_id,o.acquisition_key,o.zone_id,
            o.port_id,o.sensor,o.captured_at,o.status,o.observable_fraction,o.recipe_version,o.review_status,
            json_extract(o.data_json,'$.dataset_split') AS assigned_split,
            json_extract(o.data_json,'$.split_groups') AS split_groups,
            json_extract(o.data_json,'$.scene_complete') AS scene_complete,
            EXISTS(SELECT 1 FROM revisions r WHERE r.entity_type='observation' AND r.entity_id=o.observation_id) AS prior_review
            FROM observations o WHERE o.captured_at>=? AND o.captured_at<? ORDER BY o.observation_id""", (start, end))]
        eligible, excluded = [], []
        exposed = {(physical_pass(r["acquisition_key"]), r["port_id"], r["zone_id"])
                   for r in rows if r["scene_complete"] or r["review_status"] != "provisional" or r["prior_review"]
                   or r["observation_id"] in previously_inspected}
        for row in rows:
            identity = (row["acquisition_key"], row["port_id"], row["sensor"])
            linked = allowed.get(identity, set()) | linked_groups(json.loads(row["split_groups"] or "[]"))
            reason = None
            if identity not in allowed:
                reason = "outside_activated_train_sampling"
            elif row["assigned_split"] not in (None, "train") or linked & reserved:
                reason = "reserved_split_or_context"
            elif (row["zone_id"] not in zones
                  or zones[row["zone_id"]]["properties"].get("port_id") != row["port_id"]
                  or not row["zone_id"].endswith(("_muelles", "_fondeo", "_canal"))):
                reason = "outside_marine_queue_scope"
            elif (physical_pass(row["acquisition_key"]), row["port_id"], row["zone_id"]) in exposed:
                reason = "previous_observation_review_or_abstention"
            elif row["status"] not in ("usable", "partial") or row["observable_fraction"] is None or row["observable_fraction"] < minimum_observable:
                reason = "insufficient_local_observability"
            if reason:
                excluded.append({"observation_id": row["observation_id"], "reason": reason})
            else:
                eligible.append(row)
        assets = {}
        if eligible:
            placeholders = ",".join("?" for _ in eligible)
            for r in tx.execute(f"""SELECT observation_id,asset_id,path,sha256,size_bytes,mime_type,crs,bounds_json AS native_bounds,
                json_extract(metadata_json,'$.role') AS role,
                json_extract(metadata_json,'$.wgs84_bounds') AS bounds,
                json_extract(metadata_json,'$.display_only') AS display_only
                FROM assets WHERE state='ready' AND observation_id IN ({placeholders})
                ORDER BY asset_id""", [r["observation_id"] for r in eligible]):
                assets.setdefault(r["observation_id"], []).append(dict(r))
    candidates = []
    for row in eligible:
        evidence = assets.get(row["observation_id"], [])
        previews = [a for a in evidence if a["role"] == "preview" and a["mime_type"] == "image/png"
                    and a["display_only"] == 1 and Path(a["path"]).name == "preview.png"]
        ready = (len(previews) == 1 and any(a["role"] == "numeric" for a in evidence)
                 and any(a["role"] == "mask" and Path(a["path"]).name == "observable.tif" for a in evidence))
        if not ready:
            excluded.append({"observation_id": row["observation_id"], "reason": "missing_registered_raw_evidence"})
            continue
        if not valid_preview_georeference(previews[0]):
            excluded.append({"observation_id": row["observation_id"], "reason": "missing_or_invalid_preview_georeference"})
            continue
        try:
            # Check presence/sizes of numeric evidence without interpreting pixels.
            if any(confined_path(store.data_dir, a["path"]).stat().st_size != a["size_bytes"]
                   for a in evidence if a["role"] in {"numeric", "mask", "preview"}):
                raise ValueError("Evidence size changed")
            preview = previews[0]
            if sha256_file(confined_path(store.data_dir, preview["path"])) != preview["sha256"]:
                raise ValueError("Preview hash changed")
        except (OSError, ValueError):
            excluded.append({"observation_id": row["observation_id"], "reason": "unavailable_or_changed_evidence"})
            continue
        candidates.append({**row, "pass_id": physical_pass(row["acquisition_key"]), "preview": preview})
    selected, distribution = [], {}
    for port, sensor in STRATA:
        pool = [r for r in candidates if (r["port_id"], r["sensor"]) == (port, sensor)]
        seen_passes, months = set(), Counter()
        for _ in range(per_stratum):
            options = [r for r in pool if r["pass_id"] not in seen_passes]
            if not options:
                break
            row = min(options, key=lambda r: (months[r["captured_at"][:7]], -r["observable_fraction"],
                      r["captured_at"], r["acquisition_key"], r["zone_id"], r["recipe_version"], r["observation_id"]))
            seen_passes.add(row["pass_id"])
            months[row["captured_at"][:7]] += 1
            a = row["preview"]
            selected.append({k: row[k] for k in ("observation_id", "acquisition_key", "pass_id", "port_id", "sensor", "zone_id", "captured_at", "status", "observable_fraction")})
            selected[-1].update(zone_geometry=zones[row["zone_id"]]["geometry"],
                reason="Activated TRAIN; no prior observation review; clear local area; one zone per pass; balanced stratum and monthly diversity",
                assets=[{"asset_id": a["asset_id"], "path": a["path"], "sha256": a["sha256"],
                         "mime_type": a["mime_type"], "wgs84_bounds": json.loads(a["bounds"] or "null"),
                         "crs": a["crs"], "bounds": json.loads(a["native_bounds"] or "null")}])
        distribution[f"{port}|{sensor}"] = {"selected_passes": len(seen_passes), "requested_passes": per_stratum,
                                            "deficit": per_stratum - len(seen_passes)}
    selected_ids = {r["observation_id"] for r in selected}
    excluded.extend({"observation_id": r["observation_id"], "reason": "pass_deduplication_or_stratum_quota"}
                    for r in candidates if r["observation_id"] not in selected_ids)
    payload = {"schema_version": "blind-train-queue-v1", "split": "train", "policy": {
        "start": start, "end_exclusive": end, "minimum_observable": minimum_observable,
        "per_stratum": per_stratum, "candidate_pool": "primaries_and_explicitly_activated_reserves",
        "sampling_purpose": "TRAIN clarity acquisition, not representative validation sampling",
        "reserved_group_sha256": digest(sorted(reserved))},
        "source_sha256": {"sampling_plan": sha256_file(sampling_path), "execution_order": sha256_file(order_path),
            "ports": sha256_file(ports_path), "selector": sha256_file(Path(__file__)),
            "preview_producer": sha256_file(Path(__file__).with_name("worker.py")),
            "physical_pass_grouper": sha256_file(Path(__file__).with_name("pass_coverage.py")),
            "first_pass_receipts": digest(receipt_metadata),
            "metadata_snapshot": digest({"observations": rows, "assignments": assignments, "assets": assets})},
        "entries": selected, "distribution": distribution, "exclusions": sorted(excluded, key=lambda r: r["observation_id"])}
    return {"queue_id": "train-" + digest(payload)[:20], **payload}


def freeze_queue(manifest, path: Path):
    """Exclusive create. Identical metadata/parameters produce identical bytes."""
    _validate_queue_scopes(manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        handle.write(json_text({"sha256": digest(manifest), "manifest": manifest}) + "\n")
    return sha256_file(path)


def read_queue(path: Path, expected_sha256: str | None = None):
    if expected_sha256 and sha256_file(path) != expected_sha256:
        raise ValueError("Queue file checksum mismatch")
    envelope = json.loads(path.read_text())
    manifest = envelope["manifest"]
    payload = {k: v for k, v in manifest.items() if k != "queue_id"}
    if (envelope["sha256"] != digest(manifest) or manifest["queue_id"] != "train-" + digest(payload)[:20]
            or manifest["schema_version"] not in {"blind-train-queue-v1", "blind-train-queue-v2"}
            or manifest["split"] != "train"):
        raise ValueError("Invalid frozen TRAIN queue")
    _validate_queue_scopes(manifest)
    return manifest


def blind_entry(manifest, observation_id):
    row = next((r for r in manifest["entries"] if r["observation_id"] == observation_id), None)
    if row is None:
        raise KeyError(observation_id)
    if "search_scope" in row and manifest["schema_version"] != "blind-train-queue-v2":
        raise ValueError("Search scopes require a V2 review queue")
    validate_search_scope(row)
    keys = ("observation_id", "acquisition_key", "pass_id", "port_id", "sensor", "zone_id", "captured_at", "status", "observable_fraction", "reason", "zone_geometry")
    return {**{k: row[k] for k in keys},
            **({"search_scope": row["search_scope"]} if "search_scope" in row else {}),
            "assets": [{k: a.get(k) for k in ("asset_id", "sha256", "mime_type", "wgs84_bounds", "crs", "bounds")} for a in row["assets"]]}


def blind_asset(store, manifest, observation_id, asset_id):
    blind_entry(manifest, observation_id)
    row = next(r for r in manifest["entries"] if r["observation_id"] == observation_id)
    asset = next((a for a in row["assets"] if a["asset_id"] == asset_id), None)
    if asset is None:
        raise KeyError(asset_id)
    path = confined_path(store.data_dir, asset["path"])
    if sha256_file(path) != asset["sha256"]:
        raise ValueError("Frozen raw preview changed")
    return path
