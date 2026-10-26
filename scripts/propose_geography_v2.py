#!/usr/bin/env python3
"""Generate inactive near-quay candidates from pinned coastlines, never vessel locations."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import LineString, Point, box, mapping, shape
from shapely.ops import nearest_points, polygonize, transform, unary_union

from puertogt.history import build_tiles
from puertogt.pass_coverage import pass_key

REGIONS = {"prq": ("pacifico", 32615), "pbr": ("caribe", 32616), "stc": ("caribe", 32616)}
RADII_M = (250, 500)
COAST_MARGIN_M = 20


def read_json(path):
    return json.loads(path.read_text())


def coast_faces(source_dir, region, crs):
    path = source_dir / f"osm-coastline-{region}.json"
    receipt = read_json(source_dir / f"osm-coastline-{region}-receipt.json")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
        raise ValueError("coastline source checksum mismatch")
    forward = Transformer.from_crs(4326, crs, always_xy=True).transform
    south, west, north, east = receipt["bbox_south_west_north_east"]
    extent = transform(forward, box(west, south, east, north))
    ways = [
        element
        for element in json.loads(raw)["elements"]
        if element.get("type") == "way" and element.get("tags", {}).get("natural") == "coastline"
    ]
    lines = [
        transform(forward, LineString([(p["lon"], p["lat"]) for p in way["geometry"]]))
        for way in ways
    ]
    edges = [line.intersection(extent) for line in lines]
    # Node the full lines together with the extent boundary. Clipping first can
    # leave floating-point endpoints infinitesimally short of that boundary.
    faces = [
        face
        for face in polygonize(unary_union([extent.boundary, *lines]))
        if extent.covers(face.representative_point())
    ]
    water, land, unknown, evidence = [], [], [], []
    # OSM's directed convention is land to the left, water to the right. Probe
    # either side of original segments; no reference point chosen from imagery.
    probes = []
    for line in lines:
        for (x1, y1), (x2, y2) in zip(line.coords, list(line.coords)[1:]):
            dx, dy = x2 - x1, y2 - y1
            length = (dx * dx + dy * dy) ** 0.5
            if length < 1:
                continue
            epsilon = min(1.0, length / 8)
            x, y = (x1 + x2) / 2, (y1 + y2) / 2
            probes.append((Point(x - dy / length * epsilon, y + dx / length * epsilon), "land"))
            probes.append((Point(x + dy / length * epsilon, y - dx / length * epsilon), "water"))
    for index, face in enumerate(faces):
        votes = Counter(label for point, label in probes if face.contains(point))
        if votes.get("water") and not votes.get("land"):
            label, destination = "water", water
        elif votes.get("land") and not votes.get("water"):
            label, destination = "land", land
        else:
            label, destination = "unknown", unknown
        destination.append(face)
        evidence.append(
            {
                "face": index,
                "classification": label,
                "probe_votes": dict(votes),
                "area_m2": face.area,
            }
        )
    return {
        "water": unary_union(water),
        "land": unary_union(land),
        "unknown": unary_union(unknown),
        "coast": unary_union(edges),
        "extent": extent,
        "forward": forward,
        "inverse": Transformer.from_crs(crs, 4326, always_xy=True).transform,
        "source": receipt,
        "face_evidence": evidence,
        "way_versions": [
            {"id": way["id"], "version": way.get("version"), "timestamp": way.get("timestamp")}
            for way in ways
        ],
    }


def tile_ids(features):
    return {tile["tile_id"] for tile in build_tiles(features)}


def check_catalogue_footprints(catalogue, original, candidates):
    connection = sqlite3.connect(catalogue.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT acquisition_key,sensor,captured_at,footprint_json FROM acquisitions "
                "WHERE captured_at>='2025-01-01' AND captured_at<'2026-10-21' "
                "ORDER BY captured_at,acquisition_key"
            )
        ]
    finally:
        connection.close()
    groups = {}
    for row in rows:
        key = row["sensor"], pass_key(row["acquisition_key"], row["sensor"])
        groups.setdefault(key, []).append(shape(json.loads(row["footprint_json"])))
    passes = {key: unary_union(geometries) for key, geometries in groups.items()}
    result = {
        "window_utc": ["2025-01-01T00:00:00Z", "2026-10-21T00:00:00Z"],
        "catalogue_acquisitions": len(rows),
        "source_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
        "note": "Geometric catalogue footprint only; no quality-mask coverage or inference in new areas has been executed.",
        "items": [],
    }
    for feature in candidates:
        port = feature["properties"]["port_id"]
        geometry = shape(feature["geometry"])
        existing = unary_union(
            [shape(f["geometry"]) for f in original if f["properties"]["port_id"] == port]
        )
        project = Transformer.from_crs(4326, REGIONS[port][1], always_xy=True).transform
        area = transform(project, geometry).area
        for sensor in ("sentinel-1", "sentinel-2"):
            footprints = [
                footprint
                for (name, _), footprint in passes.items()
                if name == sensor and footprint.intersects(existing)
            ]
            fractions = [
                min(1.0, transform(project, footprint.intersection(geometry)).area / area)
                for footprint in footprints
            ]
            result["items"].append(
                {
                    "candidate_id": feature["properties"]["candidate_id"],
                    "sensor": sensor,
                    "catalogued_passes": len(footprints),
                    "passes_with_candidate_geometric_coverage_ge_90pct": sum(
                        f >= 0.9 for f in fractions
                    ),
                    "min_geometric_fraction": min(fractions) if fractions else None,
                }
            )
    return result


def draw_geometry(axis, geometry, *, color, alpha=1, linewidth=1, fill=False, linestyle="-"):
    if geometry.is_empty:
        return
    if geometry.geom_type in {"MultiPolygon", "GeometryCollection", "MultiLineString"}:
        for part in geometry.geoms:
            draw_geometry(
                axis,
                part,
                color=color,
                alpha=alpha,
                linewidth=linewidth,
                fill=fill,
                linestyle=linestyle,
            )
    elif geometry.geom_type == "Polygon":
        x, y = geometry.exterior.xy
        if fill:
            axis.fill(x, y, facecolor=color, alpha=alpha, edgecolor="none")
        axis.plot(x, y, color=color, alpha=alpha, linewidth=linewidth, linestyle=linestyle)
        for interior in geometry.interiors:
            x, y = interior.xy
            axis.plot(x, y, color=color, alpha=alpha, linewidth=linewidth, linestyle=linestyle)
    elif geometry.geom_type == "LineString":
        axis.plot(*geometry.xy, color=color, linewidth=linewidth, alpha=alpha)


def generate(config, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    output.mkdir(parents=True, exist_ok=True)
    original = read_json(config)
    regions = {
        region: coast_faces(output / "sources", region, crs)
        for region, crs in set(REGIONS.values())
    }
    candidates, audits, plot_data = [], [], []
    global_baseline_tiles = tile_ids(original["features"])
    for port in ("prq", "stc", "pbr"):
        region, crs = REGIONS[port]
        coast = regions[region]
        features = [f for f in original["features"] if f["properties"]["port_id"] == port]
        features_by_zone = {f["properties"]["zone_id"]: f for f in features}
        geometries = {
            key: transform(coast["forward"], shape(f["geometry"]))
            for key, f in features_by_zone.items()
        }
        existing = unary_union(list(geometries.values()))
        other_port_geometries = [
            transform(coast["forward"], shape(other["geometry"]))
            for other in original["features"]
            if other["properties"]["port_id"] != port
            and REGIONS[other["properties"]["port_id"]][0] == region
        ]
        exclusions = unary_union([existing, *other_port_geometries])
        quay = geometries[f"{port}_muelles"]
        if not coast["extent"].covers(quay.buffer(max(RADII_M))):
            raise ValueError(f"source extent does not cover all {port} candidates")
        safe_water = coast["water"].difference(coast["coast"].buffer(COAST_MARGIN_M))
        baseline_tiles = tile_ids(features)
        candidates_by_radius = {}
        for radius in RADII_M:
            geometry = quay.buffer(radius).intersection(safe_water).difference(exclusions)
            if not geometry.is_valid or geometry.is_empty:
                raise ValueError(f"invalid or empty {port}/{radius} candidate")
            feature = {
                "type": "Feature",
                "properties": {
                    "candidate_id": f"{port}_near_quay_{radius}m",
                    "zone_id": f"{port}_near_quay_candidate_{radius}m",
                    "port_id": port,
                    "port_name": features[0]["properties"]["port_name"],
                    "version": "2026-10-25-geography-review-candidates",
                    "status": "candidate_not_active",
                    "review_required": True,
                    "not_for_navigation": True,
                    "radius_m": radius,
                    "coast_exclusion_m": COAST_MARGIN_M,
                    "source_coastline_sha256": coast["source"]["sha256"],
                    "method": "buffer existing quay observation envelope; intersect OSM-directed water; exclude 20m coast margin and all v1 zones",
                    "role": "additional_near_quay_water_for_geographic_review",
                    "not_an_official_boundary": True,
                    "positions_of_vessels_used": False,
                    "attribution": "© OpenStreetMap contributors (ODbL-1.0)",
                },
                "geometry": mapping(transform(coast["inverse"], geometry)),
            }
            additional_tiles = tile_ids([*features, feature]) - baseline_tiles
            connector = LineString(nearest_points(quay, geometries[f"{port}_canal"]))
            connector_covered = connector.intersection(unary_union([existing, geometry])).length
            audits.append(
                {
                    "port_id": port,
                    "radius_m": radius,
                    "existing_union_area_km2": existing.area / 1e6,
                    "additional_water_area_km2": geometry.area / 1e6,
                    "baseline_tiles_per_full_pass": len(baseline_tiles),
                    "additional_tiles_per_full_pass": len(additional_tiles),
                    "additional_tile_ids": sorted(additional_tiles),
                    "candidate_tiles_per_full_pass": len(tile_ids([feature])),
                    "additional_tiles_to_three_port_grid": len(
                        tile_ids([*original["features"], feature]) - global_baseline_tiles
                    ),
                    "unknown_classification_in_buffer_m2": quay.buffer(radius)
                    .intersection(coast["unknown"])
                    .area,
                    "overlap_other_ports_m2": sum(
                        geometry.intersection(other).area for other in other_port_geometries
                    ),
                    "mapped_land_in_candidate_m2": geometry.intersection(coast["land"]).area,
                    "quay_corridor_connector_length_m": connector.length,
                    "quay_corridor_connector_covered_fraction": min(
                        1.0, connector_covered / connector.length
                    )
                    if connector.length
                    else 1.0,
                    "cost_note": "grid estimate only; full scene footprint and source availability not evaluated; cached numeric tiles can be reused, additional zone mosaics and quality still cost work",
                }
            )
            candidates.append(feature)
            candidates_by_radius[radius] = geometry
        zone_audit = [
            {
                "zone_id": key,
                "area_km2": geometry.area / 1e6,
                "mapped_land_fraction": geometry.intersection(coast["land"]).area / geometry.area,
                "mapped_water_fraction": geometry.intersection(coast["water"]).area / geometry.area,
                "unclassified_fraction": geometry.intersection(coast["unknown"]).area
                / geometry.area,
            }
            for key, geometry in geometries.items()
        ]
        gap = geometries[f"{port}_muelles"].distance(geometries[f"{port}_canal"])
        plot_data.append(
            (port, features[0]["properties"]["port_name"], coast, geometries, candidates_by_radius)
        )
        (output / f"{port}-baseline-audit.json").write_text(
            json.dumps({"zones": zone_audit, "quay_to_corridor_gap_m": gap}, indent=2) + "\n"
        )

    for i, first in enumerate(candidates):
        for second in candidates[i + 1 :]:
            if first["properties"]["port_id"] != second["properties"]["port_id"]:
                if shape(first["geometry"]).intersection(shape(second["geometry"])).area > 1e-12:
                    raise ValueError("candidates overlap between ports")
    collection = {
        "type": "FeatureCollection",
        "features": candidates,
        "note": "Two mutually exclusive radius options per port; never adopt both as additive zones.",
    }
    (output / "candidates.geojson").write_text(json.dumps(collection, indent=2) + "\n")
    receipt = {
        "version": "geography-candidates-v1",
        "active_configuration_changed": False,
        "ports_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "candidate_sha256": hashlib.sha256(
            (output / "candidates.geojson").read_bytes()
        ).hexdigest(),
        "parameters_frozen_without_vessel_positions": {
            "radii_m": RADII_M,
            "coast_margin_m": COAST_MARGIN_M,
        },
        "options": audits,
        "baseline_tiles_three_ports": len(global_baseline_tiles),
        "coastline_classification": {
            key: {
                "source_sha256": value["source"]["sha256"],
                "faces": value["face_evidence"],
                "way_versions": value["way_versions"],
            }
            for key, value in regions.items()
        },
        "scope": "inactive additional water near existing quay envelopes; not a corrected anchorage or an operational/navigation boundary",
    }
    (output / "audit.json").write_text(json.dumps(receipt, indent=2) + "\n")
    fig, axes = plt.subplots(2, 3, figsize=(16, 11), layout="constrained")
    for column, (port, name, coast, geometries, options) in enumerate(plot_data):
        existing = unary_union(list(geometries.values()))
        for row in range(2):
            axis = axes[row, column]
            extent = existing.buffer(500) if row == 0 else geometries[f"{port}_muelles"].buffer(700)
            axis.set_facecolor("#e5f2f6")
            draw_geometry(axis, coast["land"], color="#d9d9cf", fill=True)
            draw_geometry(axis, coast["unknown"], color="#decccc", fill=True)
            draw_geometry(axis, options[500], color="#3484bb", fill=True, alpha=0.38)
            draw_geometry(axis, options[250], color="#079b84", fill=True, alpha=0.65)
            for zone, geometry in geometries.items():
                draw_geometry(axis, geometry, color="#bd6b15", linewidth=1.2)
                if row == 0:
                    label = zone.split("_", 1)[1]
                    axis.annotate(
                        label,
                        (geometry.centroid.x, geometry.centroid.y),
                        fontsize=8,
                        ha="center",
                        color="#663605",
                    )
            draw_geometry(axis, coast["coast"], color="#4e585c", linewidth=0.7)
            west, south, east, north = extent.bounds
            axis.set_xlim(west, east)
            axis.set_ylim(south, north)
            axis.set_aspect("equal")
            axis.set_title(
                name + (" · contexto v1" if row == 0 else " · opciones próximas al muelle"),
                fontsize=11,
            )
            axis.tick_params(labelbottom=False, labelleft=False)
            length = 1000 if row == 0 else 250
            x, y = west + (east - west) * 0.06, south + (north - south) * 0.05
            axis.plot([x, x + length], [y, y], color="#111", linewidth=2)
            axis.annotate(
                f"{length} m", (x, y), xytext=(0, 5), textcoords="offset points", fontsize=8
            )
            axis.annotate("N ↑", (0.94, 0.94), xycoords="axes fraction", fontsize=9, ha="right")
    fig.suptitle("Opciones para revisión geográfica · sin activar ni cambiar v1", fontsize=16)
    fig.legend(
        handles=[
            Patch(facecolor="#d9d9cf", label="Tierra cartografiada OSM"),
            Patch(facecolor="#e5f2f6", label="Agua según costa OSM"),
            Patch(edgecolor="#bd6b15", facecolor="none", label="Zonas v1"),
            Patch(facecolor="#079b84", label="Opción 250 m"),
            Patch(facecolor="#3484bb", label="Opción 500 m"),
        ],
        loc="outside lower center",
        ncol=5,
        fontsize=9,
        title="© OpenStreetMap contributors · ODbL. Costa cartografiada, no observación satelital ni carta de navegación.",
        title_fontsize=8,
    )
    fig.savefig(output / "comparison.png", dpi=160, bbox_inches="tight")
    plt.close(fig)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/ports.geojson"))
    parser.add_argument("--catalogue", type=Path, default=Path("data/catalog.sqlite"))
    parser.add_argument(
        "--output", type=Path, default=Path("reports/coverage/geography-v2-candidates")
    )
    args = parser.parse_args()
    result = generate(args.config, args.output)
    if args.catalogue.is_file():
        footprint_check = check_catalogue_footprints(
            args.catalogue,
            read_json(args.config)["features"],
            read_json(args.output / "candidates.geojson")["features"],
        )
        (args.output / "existing-footprint-check.json").write_text(
            json.dumps(footprint_check, indent=2) + "\n"
        )
    print(
        json.dumps(
            {"candidate_sha256": result["candidate_sha256"], "options": result["options"]}, indent=2
        )
    )
