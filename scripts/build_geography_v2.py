#!/usr/bin/env python3
"""Build an inactive, versioned geography and measure only frozen TRAIN masks.

No database, labels, predictions, model weights, remote requests or inference are
used. The original geometry and frozen datasets are read-only inputs.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import uuid
from collections import defaultdict
from pathlib import Path

import numpy as np
import rasterio
from propose_geography_v2 import REGIONS, coast_faces, draw_geometry, tile_ids
from rasterio import Affine
from rasterio.features import geometry_mask
from shapely import set_precision
from shapely.geometry import LineString, box, mapping, shape
from shapely.ops import nearest_points, transform, unary_union

from puertogt.pass_coverage import pass_key
from puertogt.store import confined_path, sha256_file, utc_text

ROOT = Path(__file__).resolve().parents[1]
VERSION = "2026-10-26-geography-v2-train-pilot"
SOURCE_V1_SHA256 = "8eec2e8048be87361bd5c615f18cc585e450c2938bd70ab37afac40692891589"
CANDIDATES_SHA256 = "bd37617e84a9e727472263e960d0ed59f4c9a1b9b240311de86cd4e353550f3d"
COAST_RECEIPT_SHA256 = {
    "caribe": "9b0171bfd08c69e2f6cf1254a54dea665134b298c546bd9a8970271090ee398e",
    "pacifico": "6048a947f877143061723f4f67cc892876b34a146416345d6686368b870d29b0",
}
COAST_MARGIN_M = 20
TOPOLOGY_GRID_M = 0.001
TOLERANCE_M2 = 0.001


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def json_file(path):
    return json.loads(Path(path).read_bytes())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(encoded(value))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def source_coasts(candidate_dir):
    for region, digest in COAST_RECEIPT_SHA256.items():
        receipt = Path(candidate_dir) / "sources" / f"osm-coastline-{region}-receipt.json"
        if sha256_file(receipt) != digest:
            raise ValueError("Pinned coastline receipt changed")
    return {
        region: coast_faces(Path(candidate_dir) / "sources", region, crs)
        for region, crs in sorted(set(REGIONS.values()))
    }


def project_features(features, coast):
    return {
        f["properties"]["zone_id"]: transform(coast["forward"], shape(f["geometry"]))
        for f in features
    }


def build_geography(v1_path, candidate_dir):
    """Preserve 12 identities; only three quays and STC anchorage change shape."""
    v1_path, candidate_dir = Path(v1_path), Path(candidate_dir)
    if sha256_file(v1_path) != SOURCE_V1_SHA256:
        raise ValueError("This V2 recipe requires the pinned V1 geometry")
    candidate_path = candidate_dir / "candidates.geojson"
    if sha256_file(candidate_path) != CANDIDATES_SHA256:
        raise ValueError("The approved 250m candidate geometry changed")
    original = json_file(v1_path)
    coasts = source_coasts(candidate_dir)
    candidates = {
        f["properties"]["port_id"]: f
        for f in json_file(candidate_path)["features"]
        if f["properties"]["radius_m"] == 250
    }
    if set(candidates) != set(REGIONS):
        raise ValueError("Exactly three approved 250m candidates are required")
    v2 = copy.deepcopy(original)
    v2.update(
        name="Guatemala port observation zones: inactive V2 TRAIN pilot",
        version=VERSION,
        active=False,
        status="inactive_train_pilot",
        source_geometry_version=original["version"],
        source_geometry_sha256=SOURCE_V1_SHA256,
        candidate_source_sha256=CANDIDATES_SHA256,
        geometry_identity_policy="zone_id is stable; every observation must also bind geometry version and SHA256",
        attribution_policy=(
            "Existing cross-port attribution is preserved. Additional 250m water belongs to the "
            "same port's muelles and excludes every V1 zone and previously assigned addition. "
            "Shared boundaries have one owner: lexicographically first port_id. Within-port "
            "V1 overlaps remain documented; aggregate area uses union, never the sum of zones."
        ),
        not_an_official_boundary=True,
        positions_of_vessels_used=False,
        calibration_or_test_used=False,
    )
    zone_audits, port_audits = [], []
    assigned = {region: [] for region in coasts}
    additions = {}
    for port in sorted(REGIONS):
        coast = coasts[REGIONS[port][0]]
        if candidates[port]["properties"]["source_coastline_sha256"] != coast["source"]["sha256"]:
            raise ValueError("Candidate and pinned coastline provenance differ")
        old_region = [
            f
            for f in original["features"]
            if REGIONS[f["properties"]["port_id"]][0] == REGIONS[port][0]
        ]
        exclusion = unary_union(list(project_features(old_region, coast).values()))
        raw = transform(coast["forward"], shape(candidates[port]["geometry"]))
        safe_water = coast["water"].difference(coast["coast"].buffer(COAST_MARGIN_M))
        # Recheck the source candidate's defining constraints; lexicographic
        # ownership also makes future overlapping candidates deterministic.
        addition = raw.intersection(safe_water).difference(exclusion)
        addition = addition.difference(unary_union(assigned[REGIONS[port][0]]))
        if addition.is_empty or not addition.is_valid:
            raise ValueError(f"Invalid or empty water addition for {port}")
        if raw.symmetric_difference(addition).area > TOLERANCE_M2:
            raise ValueError("Pinned candidate no longer satisfies exclusive water attribution")
        assigned[REGIONS[port][0]].append(addition)
        additions[port] = addition
    for feature in v2["features"]:
        props = feature["properties"]
        port, zone = props["port_id"], props["zone_id"]
        coast = coasts[REGIONS[port][0]]
        old = transform(coast["forward"], shape(feature["geometry"]))
        new = old
        topology_change_m2 = 0.0
        change = "unchanged_shape"
        if props["zone_type"] == "muelles":
            new = unary_union([old, additions[port]])
            change = "add_exclusive_250m_near_quay_water"
        elif zone == "stc_fondeo":
            new = old.intersection(coast["water"]).difference(coast["coast"].buffer(COAST_MARGIN_M))
            change = "exclude_OSM_land_unknown_and_20m_coast_margin"
        if new.is_empty or not new.is_valid or new.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError(f"Invalid V2 geometry: {zone}")
        # Preserve exact coordinates for the eight unmodified shapes.
        if change != "unchanged_shape":
            # A millimetre grid removes projection round-off slivers where
            # candidate and existing boundaries touch. It is not a claim of
            # millimetre shoreline accuracy. Validate serialized coordinates too.
            before_normalization = new
            new = set_precision(new, TOPOLOGY_GRID_M)
            geographic = transform(coast["inverse"], new)
            if not geographic.is_valid:
                raise ValueError(f"Invalid serialized V2 topology: {zone}")
            feature["geometry"] = mapping(geographic)
            new = transform(coast["forward"], geographic)
            if not new.is_valid:
                raise ValueError(f"Invalid reprojected V2 topology: {zone}")
            topology_change_m2 = new.symmetric_difference(before_normalization).area
            if topology_change_m2 > before_normalization.length * TOPOLOGY_GRID_M * 2:
                raise ValueError("Topology normalization changed more than its rounding tolerance")
        props.update(
            version=VERSION,
            geometry_status="inactive_train_pilot",
            active=False,
            source_geometry_sha256=SOURCE_V1_SHA256,
            source_geometry_version=original["version"],
            source_coastline_sha256=coast["source"]["sha256"],
            geometry_change=change,
            geometry_scope_key=f"{VERSION}:{zone}",
            topology_precision_grid_m=TOPOLOGY_GRID_M if change != "unchanged_shape" else None,
            not_an_official_boundary=True,
            review_required=True,
            not_for_navigation=True,
            attribution="© OpenStreetMap contributors (ODbL-1.0); V1 operator references retained",
        )
        if change != "unchanged_shape":
            props["source_method_v1"] = props["method"]
            props["method"] = (
                "V2 analytic pilot: " + change + ". Pinned OSM-directed coastline; "
                "20m analytical coast margin. No vessel positions or port jurisdiction inferred."
            )
        zone_audits.append(
            {
                "zone_id": zone,
                "port_id": port,
                "change": change,
                "v1_area_m2": old.area,
                "v2_area_m2": new.area,
                "added_area_m2": new.difference(old).area,
                "removed_area_m2": old.difference(new).area,
                "v1_mapped_water_m2": old.intersection(coast["water"]).area,
                "v2_mapped_water_m2": new.intersection(coast["water"]).area,
                "v1_mapped_land_m2": old.intersection(coast["land"]).area,
                "v2_mapped_land_m2": new.intersection(coast["land"]).area,
                "topology_normalization_symmetric_difference_m2": topology_change_m2,
            }
        )
    for port in sorted(REGIONS):
        coast = coasts[REGIONS[port][0]]
        old_f = [f for f in original["features"] if f["properties"]["port_id"] == port]
        new_f = [f for f in v2["features"] if f["properties"]["port_id"] == port]
        old, new = project_features(old_f, coast), project_features(new_f, coast)
        old_union, new_union = unary_union(list(old.values())), unary_union(list(new.values()))
        connector = LineString(nearest_points(old[f"{port}_muelles"], old[f"{port}_canal"]))
        other = unary_union(
            [
                transform(coast["forward"], shape(f["geometry"]))
                for f in v2["features"]
                if f["properties"]["port_id"] != port
                and REGIONS[f["properties"]["port_id"]][0] == REGIONS[port][0]
            ]
        )
        overlap = new_union.intersection(other).area
        if overlap > TOLERANCE_M2:
            raise ValueError("V2 has cross-port area overlap")
        port_audits.append(
            {
                "port_id": port,
                "crs": f"EPSG:{REGIONS[port][1]}",
                "v1_union_area_m2": old_union.area,
                "v2_union_area_m2": new_union.area,
                "v1_water_union_area_m2": old_union.intersection(coast["water"]).area,
                "v2_water_union_area_m2": new_union.intersection(coast["water"]).area,
                "additional_water_m2": new_union.difference(old_union)
                .intersection(coast["water"])
                .area,
                "approved_candidate_water_m2": additions[port].area,
                "v1_within_port_overlap_m2": max(
                    0.0, sum(g.area for g in old.values()) - old_union.area
                ),
                "v2_within_port_overlap_m2": max(
                    0.0, sum(g.area for g in new.values()) - new_union.area
                ),
                "cross_port_overlap_m2": overlap,
                "v1_quay_canal_gap_m": old[f"{port}_muelles"].distance(old[f"{port}_canal"]),
                "v2_quay_canal_gap_m": new[f"{port}_muelles"].distance(new[f"{port}_canal"]),
                "v1_connector_covered_by_v2_fraction": (
                    connector.intersection(new_union).length / connector.length
                    if connector.length
                    else 1.0
                ),
                "v1_tile_ids": sorted(tile_ids(old_f)),
                "v2_tile_ids": sorted(tile_ids(new_f)),
            }
        )
    audit = {
        "schema": "geography-v2-build-v1",
        "geometry_version": VERSION,
        "v1_sha256": SOURCE_V1_SHA256,
        "v2_sha256": hashlib.sha256(encoded(v2)).hexdigest(),
        "candidates_sha256": CANDIDATES_SHA256,
        "active": False,
        "coastline_sources": {region: c["source"] for region, c in coasts.items()},
        "coastline_receipt_sha256": COAST_RECEIPT_SHA256,
        "parameters": {
            "expansion_m": 250,
            "coast_exclusion_m": COAST_MARGIN_M,
            "topology_precision_grid_m": TOPOLOGY_GRID_M,
        },
        "zone_count": len(v2["features"]),
        "zones": zone_audits,
        "ports": port_audits,
        "v1_global_tile_ids": sorted(tile_ids(original["features"])),
        "v2_global_tile_ids": sorted(tile_ids(v2["features"])),
        "navigation_or_port_authority_validated": False,
        "calibration_or_test_used": False,
    }
    return v2, audit, coasts


def train_records(roots):
    """Only allow acquisitions explicitly included in nonempty TRAIN manifests."""
    groups, sources = defaultdict(list), []
    seen_acquisitions = set()
    for root in roots:
        root = Path(root).resolve()
        path = root / "manifest.json"
        manifest_bytes = path.read_bytes()
        manifest = json.loads(manifest_bytes)
        samples = manifest.get("samples", [])
        if manifest.get("split") != "train" or not samples:
            raise ValueError("Only a nonempty TRAIN manifest is permitted")
        if any(s.get("split", "train") != "train" for s in samples):
            raise ValueError("Every sample must belong to TRAIN")
        acquisitions = manifest.get("history_inference", {}).get("acquisitions", [])
        sample_keys = {s["acquisition_key"] for s in samples}
        if {a["acquisition_key"] for a in acquisitions} != sample_keys:
            raise ValueError("TRAIN acquisitions and complete retained evidence must match exactly")
        sources.append(
            {
                "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "split": "train",
                "acquisitions": sorted(sample_keys),
            }
        )
        for acquisition in acquisitions:
            key = acquisition["acquisition_key"]
            if key in seen_acquisitions:
                raise ValueError("TRAIN acquisition repeated across input manifests")
            seen_acquisitions.add(key)
            entries = acquisition.get("tiles", [])
            if [e["tile"]["tile_id"] for e in entries] != acquisition["expected_tile_ids"] or len(
                {e["tile"]["tile_id"] for e in entries}
            ) != len(entries):
                raise ValueError("Incomplete or duplicated frozen tile set")
            for entry in entries:
                bundle = entry["bundle"]
                if bundle["metadata"].get("acquisition_key") != key:
                    raise ValueError("Frozen tile acquisition mismatch")
                sensor = bundle["sensor"]
                if sensor not in {"sentinel-1", "sentinel-2"}:
                    raise ValueError("Unsupported TRAIN sensor")
                if {s["sensor"] for s in samples if s["acquisition_key"] == key} != {sensor}:
                    raise ValueError("TRAIN sample sensor and numeric evidence differ")
                identity = (sensor, pass_key(key, sensor), bundle["crs"])
                groups[identity].append(
                    {
                        "root": root,
                        "acquisition_key": key,
                        "captured_at": acquisition["captured_at"],
                        "entry": entry,
                    }
                )
    return groups, sources


def native_mosaic(records, bounds, *, max_pixels=25_000_000):
    """Use 448px cores on one unchanged 10m grid; binary OR deduplicates slices."""
    west, south, east, north = bounds
    west, south = math.floor(west / 10) * 10, math.floor(south / 10) * 10
    east, north = math.ceil(east / 10) * 10, math.ceil(north / 10) * 10
    width, height = int((east - west) / 10), int((north - south) / 10)
    if width <= 0 or height <= 0 or width * height > max_pixels:
        raise ValueError("Pilot pixel budget exceeded or empty extent")
    grid = Affine(10, 0, west, 0, -10, north)
    frame = np.zeros((height, width), dtype=bool)
    valid, observable = frame.copy(), frame.copy()
    crs_values = {r["entry"]["bundle"]["crs"] for r in records}
    recipes = {
        (
            r["entry"]["bundle"]["sensor"],
            r["entry"]["bundle"]["product_level"],
            r["entry"]["bundle"]["preprocessing_version"],
            json.dumps(r["entry"]["bundle"]["metadata"].get("quality_model"), sort_keys=True),
        )
        for r in records
    }
    if len(crs_values) != 1 or len(recipes) != 1:
        raise ValueError("No mixing of incompatible mask recipes or grids")
    pins = []
    duplicate_core_pixels = 0
    for record in records:
        entry, root = record["entry"], record["root"]
        tile, bundle = entry["tile"], entry["bundle"]
        if (tile["size"], tile["margin"], tile["pixel_size"]) != (512, 32, 10):
            raise ValueError("Only frozen 512px tiles with 448px cores are permitted")
        left, bottom, right, top = tile["core_bounds"]
        expected = Affine(10, 0, left - 320, 0, -10, top + 320)
        if (right - left, top - bottom) != (4480, 4480) or expected != Affine(*tile["transform"]):
            raise ValueError("Frozen core grid changed")
        if Affine(*bundle["transform"]) != expected or bundle["crs"] != tile["crs"]:
            raise ValueError("Frozen bundle grid changed")
        arrays = {}
        for role in ("observable", "valid"):
            relative = bundle["mask_paths"][role]
            path = confined_path(root, relative)
            digest = entry["file_sha256"][relative]
            if sha256_file(path) != digest:
                raise ValueError("Frozen TRAIN mask checksum mismatch")
            with rasterio.open(path) as src:
                if (
                    src.shape != (512, 512)
                    or src.count != 1
                    or src.transform != expected
                    or str(src.crs) != bundle["crs"]
                ):
                    raise ValueError("No resampling or replacement masks permitted")
                arrays[role] = src.read(1, window=((32, 480), (32, 480))) > 0
            pins.append(
                {
                    "acquisition_key": record["acquisition_key"],
                    "tile_id": tile["tile_id"],
                    "role": role,
                    "path": relative,
                    "sha256": digest,
                    "size_bytes": path.stat().st_size,
                }
            )
        if np.any(arrays["observable"] & ~arrays["valid"]):
            raise ValueError("Observable mask contains invalid pixels")
        row, column = (north - top) / 10, (left - west) / 10
        if row != int(row) or column != int(column):
            raise ValueError("Masks are not on the evaluation pixel lattice")
        row, column = int(row), int(column)
        # A crop outside the requested evaluation extent has no contribution.
        y0, x0, y1, x1 = (
            max(row, 0),
            max(column, 0),
            min(row + 448, height),
            min(column + 448, width),
        )
        if y0 >= y1 or x0 >= x1:
            continue
        target = np.s_[y0:y1, x0:x1]
        source = np.s_[y0 - row : y1 - row, x0 - column : x1 - column]
        duplicate_core_pixels += int(frame[target].sum())
        frame[target] = True
        valid[target] |= arrays["valid"][source]
        observable[target] |= arrays["observable"][source]
    return {
        "frame": frame,
        "valid": valid,
        "observable": observable,
        "transform": grid,
        "pins": pins,
        "duplicate_core_pixels_removed": duplicate_core_pixels,
    }


def measure_area(geometry, mosaic):
    """Geometric area and a pixel-centre estimate on the native 10m lattice."""
    if geometry.is_empty:
        inside = np.zeros_like(mosaic["frame"])
    else:
        inside = geometry_mask(
            [geometry], out_shape=mosaic["frame"].shape, transform=mosaic["transform"], invert=True
        )
    total, frame, valid, observed = [
        int((inside & mask).sum())
        for mask in (np.ones_like(inside), mosaic["frame"], mosaic["valid"], mosaic["observable"])
    ]
    return {
        "geometry_area_m2": geometry.area,
        "water_pixel_count": total,
        "pixel_area_m2": total * 100,
        "frame_pixel_count": frame,
        "valid_pixel_count": valid,
        "observable_pixel_count": observed,
        "observable_water_m2": observed * 100,
        "outside_frame_pixel_count": total - frame,
        "outside_frame_water_m2": (total - frame) * 100,
        "invalid_in_frame_pixel_count": frame - valid,
        "valid_but_unobservable_pixel_count": valid - observed,
        "observable_fraction_of_water_pixels": observed / total if total else None,
        "observable_fraction_of_framed_water_pixels": observed / frame if frame else None,
    }


def pilot(v1_path, v2_path, candidate_dir, train_roots):
    expected, build, coasts = build_geography(v1_path, candidate_dir)
    if json_file(v2_path) != json.loads(encoded(expected)):
        raise ValueError("Pilot V2 differs from the reproducible inactive configuration")
    groups, sources = train_records(train_roots)
    original = json_file(v1_path)
    items, evidence, previews = [], [], {}
    for (sensor, key, crs), records in sorted(groups.items()):
        ports = sorted(port for port, (_, epsg) in REGIONS.items() if crs == f"EPSG:{epsg}")
        coast = coasts[REGIONS[ports[0]][0]]
        old = project_features(
            [f for f in original["features"] if f["properties"]["port_id"] in ports], coast
        )
        new = project_features(
            [f for f in expected["features"] if f["properties"]["port_id"] in ports], coast
        )
        extent = unary_union([*old.values(), *new.values()]).bounds
        mosaic = native_mosaic(records, extent)
        evidence.append(
            {
                "sensor": sensor,
                "pass_key": key,
                "crs": crs,
                "duplicate_core_pixels_removed": mosaic["duplicate_core_pixels_removed"],
                "masks": mosaic["pins"],
            }
        )
        for port in ports:
            old_port = unary_union([g for zone, g in old.items() if zone.startswith(port + "_")])
            new_port = unary_union([g for zone, g in new.items() if zone.startswith(port + "_")])
            water = coast["water"]
            additions = new_port.difference(old_port).intersection(water)
            row = {
                "sensor": sensor,
                "pass_key": key,
                "port_id": port,
                "captured_at": min(r["captured_at"] for r in records),
                "acquisition_keys": sorted({r["acquisition_key"] for r in records}),
                "v1_water": measure_area(old_port.intersection(water), mosaic),
                "v2_water": measure_area(new_port.intersection(water), mosaic),
                "additional_water": measure_area(additions, mosaic),
                "excluded_water": measure_area(
                    old_port.difference(new_port).intersection(water), mosaic
                ),
                "zones": [],
            }
            for zone in sorted(z for z in old if z.startswith(port + "_")):
                row["zones"].append(
                    {
                        "zone_id": zone,
                        "v1_water": measure_area(old[zone].intersection(water), mosaic),
                        "v2_water": measure_area(new[zone].intersection(water), mosaic),
                    }
                )
            items.append(row)
            if sensor == "sentinel-2" and (
                port not in previews or row["captured_at"] < previews[port]["row"]["captured_at"]
            ):
                previews[port] = {
                    "row": row,
                    "mosaic": mosaic,
                    "coast": coast,
                    "old": old_port,
                    "new": new_port,
                    "addition": additions,
                    "quay": new[f"{port}_muelles"],
                }
    summary = []
    for port in sorted(REGIONS):
        for sensor in ("sentinel-1", "sentinel-2"):
            rows = [r for r in items if r["port_id"] == port and r["sensor"] == sensor]
            areas = [r["additional_water"]["observable_water_m2"] for r in rows]
            summary.append(
                {
                    "port_id": port,
                    "sensor": sensor,
                    "train_passes": len(rows),
                    "additional_water_observable_m2_per_pass": {
                        "min": min(areas) if areas else None,
                        "median": float(np.median(areas)) if areas else None,
                        "max": max(areas) if areas else None,
                    },
                    "passes_with_added_water_ge90pct_observable": sum(
                        (r["additional_water"]["observable_fraction_of_water_pixels"] or 0) >= 0.9
                        for r in rows
                    ),
                    "passes_with_added_water_outside_frame": sum(
                        r["additional_water"]["outside_frame_pixel_count"] > 0 for r in rows
                    ),
                    "passes_with_v2_water_outside_frame": sum(
                        r["v2_water"]["outside_frame_pixel_count"] > 0 for r in rows
                    ),
                    "maximum_v2_water_outside_frame_m2": max(
                        (r["v2_water"]["outside_frame_water_m2"] for r in rows), default=None
                    ),
                    "minimum_added_water_frame_fraction": min(
                        (
                            1
                            - r["additional_water"]["outside_frame_pixel_count"]
                            / r["additional_water"]["water_pixel_count"]
                            for r in rows
                            if r["additional_water"]["water_pixel_count"]
                        ),
                        default=None,
                    ),
                }
            )
    return {
        "schema": "geography-v2-train-pilot-v1",
        "generated_at": utc_text(),
        "geometry_version": VERSION,
        "v1_sha256": SOURCE_V1_SHA256,
        "v2_sha256": sha256_file(v2_path),
        "geometry_build_sha256": hashlib.sha256(encoded(build)).hexdigest(),
        "script_sha256": sha256_file(Path(__file__)),
        "coast_helper_sha256": sha256_file(ROOT / "scripts/propose_geography_v2.py"),
        "active": False,
        "calibration_or_test_opened": False,
        "labels_or_predictions_read": False,
        "inference_run": False,
        "new_downloads": 0,
        "database_opened": False,
        "training_sources": sources,
        "acquisition_count": sum(len(s["acquisitions"]) for s in sources),
        "physical_pass_count": len({(sensor, key) for sensor, key, _ in groups}),
        "verified_mask_files": sum(len(e["masks"]) for e in evidence),
        "verified_mask_bytes": sum(m["size_bytes"] for e in evidence for m in e["masks"]),
        "summary": summary,
        "items": items,
        "evidence": evidence,
        "method": "Frozen TRAIN targets only; same-pass binary union of native 448px cores; water from pinned OSM; 10m pixel centres; no interpolation",
        "limits": [
            "Selected development sample, not a representative temporal coverage or accuracy validation.",
            "OSM coastline is a dated geographic reference, not port authority, bathymetry or navigation limits.",
            "Existing model-derived quality masks are not independently validated here.",
            "Frame means retained core bounds; valid and observable masks expose missing/unsuitable pixels inside them.",
            "Area sums across dates would be repeated area-observations, not distinct square kilometres or new visits.",
            "V1 observations and frozen labels retain their original geographic meaning.",
        ],
    }, previews


def draw_pilot(previews, path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from matplotlib.ticker import MaxNLocator

    figure, axes = plt.subplots(1, 3, figsize=(15, 5.4))
    figure.subplots_adjust(left=0.06, right=0.99, top=0.86, bottom=0.23, wspace=0.28)
    for axis, port in zip(axes, ("prq", "stc", "pbr"), strict=True):
        if port not in previews:
            axis.text(
                0.5,
                0.5,
                f"{port.upper()}\nSin captura óptica TRAIN en estos inputs",
                ha="center",
                va="center",
                transform=axis.transAxes,
            )
            axis.set_axis_off()
            continue
        preview = previews[port]
        coast, old, new, mosaic = (preview[k] for k in ("coast", "old", "new", "mosaic"))
        # The figure shows the first chronological optical TRAIN pass, chosen
        # without using detections, labels or the measured visibility result.
        geometry = preview["addition"]
        xmin, ymin, xmax, ymax = preview["quay"].bounds
        extent = box(xmin - 300, ymin - 300, xmax + 300, ymax + 300)
        draw_geometry(axis, coast["land"].intersection(extent), color="#d0c9bc", fill=True)
        grid = mosaic["transform"]
        water = geometry_mask(
            [coast["water"]], out_shape=mosaic["frame"].shape, transform=grid, invert=True
        )
        observable = mosaic["observable"] & water
        top_left = grid * (0, 0)
        lower_right = grid * (observable.shape[1], observable.shape[0])
        axis.imshow(
            np.ma.masked_where(~observable, observable),
            cmap="Blues",
            alpha=0.28,
            vmin=0,
            vmax=1,
            extent=[top_left[0], lower_right[0], lower_right[1], top_left[1]],
        )
        draw_geometry(axis, old, color="#777777", linewidth=1, linestyle="--")
        draw_geometry(axis, new, color="#185b91", linewidth=1.2)
        draw_geometry(axis, geometry, color="#008d64", alpha=0.5, fill=True)
        axis.set(
            xlim=(extent.bounds[0], extent.bounds[2]),
            ylim=(extent.bounds[1], extent.bounds[3]),
            title=f"{port.upper()} · {preview['row']['captured_at'][:10]}",
            xlabel="Este UTM (m)",
            ylabel="Norte UTM (m)",
        )
        axis.set_aspect("equal")
        axis.ticklabel_format(style="plain", useOffset=False)
        axis.xaxis.set_major_locator(MaxNLocator(3))
        axis.tick_params(labelsize=7)
    figure.suptitle("Piloto V2 inactivo · primera captura óptica TRAIN por puerto", fontsize=14)
    figure.legend(
        handles=[
            Patch(facecolor="#d0c9bc", label="Tierra OSM"),
            Patch(facecolor="#d1e4f1", label="Agua observable (máscara)"),
            Patch(facecolor="#008d64", label="Agua añadida 250 m"),
            Patch(facecolor="none", edgecolor="#185b91", label="Contorno V2"),
            Patch(facecolor="none", edgecolor="#777777", linestyle="--", label="Contorno V1"),
        ],
        loc="lower center",
        bbox_to_anchor=(0.5, 0.065),
        ncol=5,
        fontsize=9,
    )
    figure.text(
        0.5,
        0.035,
        "© OpenStreetMap contributors, ODbL-1.0 · Contains modified Copernicus Sentinel data 2025 · Cobertura de área, sin evaluación de detección",
        ha="center",
        fontsize=8,
    )
    figure.savefig(path, dpi=130)
    plt.close(figure)


def draw_comparison(v1, v2, coasts, path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from matplotlib.ticker import MaxNLocator

    figure, axes = plt.subplots(2, 3, figsize=(15, 10))
    figure.subplots_adjust(left=0.06, right=0.99, top=0.92, bottom=0.16, wspace=0.28, hspace=0.25)
    for column, port in enumerate(("prq", "stc", "pbr")):
        coast = coasts[REGIONS[port][0]]
        old = project_features(
            [f for f in v1["features"] if f["properties"]["port_id"] == port], coast
        )
        new = project_features(
            [f for f in v2["features"] if f["properties"]["port_id"] == port], coast
        )
        old_union, new_union = unary_union(list(old.values())), unary_union(list(new.values()))
        added, removed = new_union.difference(old_union), old_union.difference(new_union)
        for row, scope in enumerate((old_union, new[f"{port}_muelles"])):
            axis = axes[row, column]
            xmin, ymin, xmax, ymax = scope.bounds
            margin = 300 if row else 500
            extent = box(xmin - margin, ymin - margin, xmax + margin, ymax + margin)
            draw_geometry(axis, coast["land"].intersection(extent), color="#ddd7cd", fill=True)
            draw_geometry(axis, removed.intersection(extent), color="#d25151", alpha=0.5, fill=True)
            draw_geometry(axis, added.intersection(extent), color="#008d64", alpha=0.5, fill=True)
            draw_geometry(axis, old_union, color="#777777", linewidth=1, linestyle="--")
            draw_geometry(axis, new_union, color="#185b91", linewidth=1.2)
            axis.set(
                xlim=(extent.bounds[0], extent.bounds[2]),
                ylim=(extent.bounds[1], extent.bounds[3]),
                title=f"{port.upper()} · {'conjunto de zonas' if not row else 'ampliación de muelles'}",
                xlabel="Este UTM (m)",
                ylabel="Norte UTM (m)",
            )
            axis.set_aspect("equal")
            axis.ticklabel_format(style="plain", useOffset=False)
            axis.xaxis.set_major_locator(MaxNLocator(3))
            axis.tick_params(labelsize=7)
    figure.suptitle("Propuesta V2 inactiva · comparación geográfica con V1", fontsize=15)
    figure.legend(
        handles=[
            Patch(facecolor="#ddd7cd", label="Tierra OSM"),
            Patch(facecolor="#d25151", label="Área excluida"),
            Patch(facecolor="#008d64", label="Agua añadida 250 m"),
            Patch(facecolor="none", edgecolor="#185b91", label="V2"),
            Patch(facecolor="none", edgecolor="#777777", linestyle="--", label="V1"),
        ],
        loc="lower center",
        bbox_to_anchor=(0.5, 0.055),
        ncol=5,
        fontsize=10,
    )
    figure.text(
        0.5,
        0.025,
        "© OpenStreetMap contributors, ODbL-1.0 · Costa fijada el 26-10-2026 UTC · Sin posiciones de buques · No son límites oficiales",
        ha="center",
        fontsize=9,
    )
    figure.savefig(path, dpi=120)
    plt.close(figure)


def compact_summary(build, report, output):
    return {
        "schema": "geography-v2-pilot-summary-v1",
        "geometry_version": VERSION,
        "status": "inactive_train_pilot",
        "active": False,
        "v1_sha256": SOURCE_V1_SHA256,
        "v2_sha256": report["v2_sha256"],
        "pilot_sha256": hashlib.sha256(encoded(report)).hexdigest(),
        "generated_at": report["generated_at"],
        "zone_count": build["zone_count"],
        "ports": build["ports"],
        "zones": build["zones"],
        "train_coverage": report["summary"],
        "train_acquisitions": report["acquisition_count"],
        "train_physical_passes": report["physical_pass_count"],
        "verified_mask_files": report["verified_mask_files"],
        "calibration_or_test_opened": False,
        "inference_run": False,
        "labels_or_predictions_read": False,
        "active_configuration_changed": False,
        "source_attribution": "© OpenStreetMap contributors, ODbL-1.0; Contains modified Copernicus Sentinel data 2025",
        "geography_image": "comparison.png",
        "pilot_image": "pilot.png",
        "assets": {
            name: {
                "sha256": sha256_file(Path(output) / name),
                "size_bytes": (Path(output) / name).stat().st_size,
                "mime_type": "image/png",
            }
            for name in ("comparison.png", "pilot.png")
        },
        "limits": report["limits"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "pilot"))
    parser.add_argument("--v1", type=Path, default=ROOT / "config/ports.geojson")
    parser.add_argument("--v2", type=Path, default=ROOT / "config/ports-v2.geojson")
    parser.add_argument(
        "--candidates", type=Path, default=ROOT / "reports/coverage/geography-v2-candidates"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "reports/coverage/geography-v2-pilot")
    parser.add_argument("--train-root", type=Path, action="append")
    args = parser.parse_args()
    if args.v1.resolve() == args.v2.resolve():
        parser.error("V1 cannot be an output")
    args.output.mkdir(parents=True, exist_ok=True)
    if args.command == "build":
        config, audit, coasts = build_geography(args.v1, args.candidates)
        write_json(args.v2, config)
        write_json(args.output / "geometry-audit.json", audit)
        draw_comparison(json_file(args.v1), config, coasts, args.output / "comparison.png")
        print(
            json.dumps(
                {
                    "geometry_version": VERSION,
                    "zones": len(config["features"]),
                    "active": False,
                    "v2_sha256": sha256_file(args.v2),
                }
            )
        )
    else:
        if not args.train_root:
            parser.error("At least one explicit --train-root is required")
        report, previews = pilot(args.v1, args.v2, args.candidates, args.train_root)
        write_json(args.output / "pilot.json", report)
        build = json_file(args.output / "geometry-audit.json")
        if build["v2_sha256"] != report["v2_sha256"]:
            raise ValueError("Build receipt differs from current pilot geometry")
        draw_pilot(previews, args.output / "pilot.png")
        write_json(args.output / "summary.json", compact_summary(build, report, args.output))
        write_json(
            args.output / "train-selection.json",
            {
                "schema": "geography-v2-train-selection-v1",
                "split": "train",
                "geometry_version": VERSION,
                "geometry_sha256": report["v2_sha256"],
                "acquisition_keys": sorted(
                    {key for source in report["training_sources"] for key in source["acquisitions"]}
                ),
                "source_manifests": [
                    {"path": source["path"], "sha256": source["sha256"]}
                    for source in report["training_sources"]
                ],
                "status": "selection_only_no_processing_plan_executed",
            },
        )
        print(
            json.dumps(
                {
                    "acquisitions": report["acquisition_count"],
                    "physical_passes": report["physical_pass_count"],
                    "verified_mask_files": report["verified_mask_files"],
                    "summary": report["summary"],
                }
            )
        )


if __name__ == "__main__":
    main()
