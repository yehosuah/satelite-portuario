"""Freeze a derived TRAIN queue with the pinned PRQ water search margin."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

import shapely
from pyproj import CRS, Transformer
from shapely.geometry import box, mapping, shape

from puertogt.review_queue import digest, freeze_queue, read_queue

ROOT = Path(__file__).resolve().parents[1]
PARENT_QUEUE_RELATIVE = "reports/review-queue/train-primaries-final-v2/queue.json"
PROPOSAL_RELATIVE = "reports/coverage/anchorage-search-margins/proposal.json"
PARENT_QUEUE_SHA256 = "35c92bca87c79d64833edde6db1870f0b00fd2cc54a0177857e150a84a33d5bc"
PROPOSAL_SHA256 = "a3bafdf405d49047eecb20abb6c942e4c32597082af2da54afed74971efdef25"
RADIUS_M = 500
METRIC_CRS = CRS.from_epsg(32615)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _polygon(value, label):
    geometry = shape(value)
    coordinates = shapely.get_coordinates(geometry)
    if (geometry.geom_type not in {"Polygon", "MultiPolygon"} or geometry.is_empty
            or not geometry.is_valid
            or not all(math.isfinite(float(v)) for point in coordinates for v in point)
            or not all(-180 <= x <= 180 and -90 <= y <= 90 for x, y in coordinates)):
        raise ValueError(f"Invalid WGS84 polygon: {label}")
    return geometry


def _project(geometry, source, target):
    transformer = Transformer.from_crs(source, target, always_xy=True)
    return shapely.transform(geometry, transformer.transform, interleaved=False)


def _search_scope(entry, water_margin):
    reference = _polygon(entry["zone_geometry"], "reference zone")
    combined = _project(reference.union(water_margin), 4326, METRIC_CRS)
    original = combined
    if not entry.get("assets"):
        raise ValueError("Search scope requires a native preview frame")
    for asset in entry["assets"]:
        bounds = asset.get("bounds")
        if (not isinstance(bounds, list) or len(bounds) != 4
                or not all(type(v) in (int, float) and math.isfinite(v) for v in bounds)
                or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]):
            raise ValueError("Invalid native preview bounds")
        crs = CRS.from_user_input(asset["crs"])
        native = _project(combined, METRIC_CRS, crs)
        combined = _project(native.intersection(box(*bounds)), crs, METRIC_CRS)
    if combined.geom_type not in {"Polygon", "MultiPolygon"} or combined.is_empty:
        raise ValueError("Search scope has no polygon inside the preview frames")
    # Area tolerance only removes projection round-off from the clipping flag.
    clipped = original.difference(combined).area > 1e-6
    geometry = mapping(_project(combined, METRIC_CRS, 4326))
    _polygon(geometry, "clipped search scope")
    return {
        "schema_version": "water-search-scope-v1",
        "radius_m": RADIUS_M,
        "geometry": geometry,
        "geometry_sha256": digest(geometry),
        "reference_zone_sha256": digest(entry["zone_geometry"]),
        "source_sha256": PROPOSAL_SHA256,
        "frame_clipped": clipped,
        "observability": "not_measured",
    }


def build_search_queue(
    parent_queue: Path = ROOT / PARENT_QUEUE_RELATIVE,
    proposal_path: Path = ROOT / PROPOSAL_RELATIVE,
):
    """Read only the pinned queue, geographic proposal and this builder's bytes."""
    parent_queue, proposal_path = Path(parent_queue), Path(proposal_path)
    if _sha256(parent_queue) != PARENT_QUEUE_SHA256:
        raise ValueError("Pinned parent queue checksum mismatch")
    if _sha256(proposal_path) != PROPOSAL_SHA256:
        raise ValueError("Pinned search proposal checksum mismatch")
    parent = read_queue(parent_queue, PARENT_QUEUE_SHA256)
    if parent["schema_version"] != "blind-train-queue-v1" or parent["split"] != "train":
        raise ValueError("Derivation requires the original TRAIN v1 queue")
    if (len(parent["entries"]) != 12
            or len({e["observation_id"] for e in parent["entries"]}) != 12
            or any("search_scope" in e for e in parent["entries"])):
        raise ValueError("Derivation requires 12 unique entries without an existing search scope")
    proposal = json.loads(proposal_path.read_text())
    if (proposal.get("schema") != "anchorage-search-margins-proposal-v1"
            or proposal.get("active") is not False
            or proposal.get("active_configuration_changed") is not False):
        raise ValueError("Invalid inactive search proposal")
    source_hashes = {s["path"]: s["sha256"] for s in proposal["sources"]}
    if (source_hashes.get(PARENT_QUEUE_RELATIVE) != PARENT_QUEUE_SHA256
            or source_hashes.get("config/ports.geojson") != parent["source_sha256"]["ports"]):
        raise ValueError("Proposal provenance differs from the frozen parent queue")
    margins = [f for f in proposal["geometries"]["features"]
               if f["properties"].get("port_id") == "prq"
               and f["properties"].get("zone_id") == "prq_fondeo"
               and f["properties"].get("radius_m") == RADIUS_M]
    if len(margins) != 1:
        raise ValueError("Expected exactly one pinned PRQ 500 m water margin")
    margin = _polygon(margins[0]["geometry"], "pinned water margin")
    manifest = copy.deepcopy(parent)
    manifest.pop("queue_id")
    extended = []
    for entry in manifest["entries"]:
        if entry["port_id"] == "prq" and entry["zone_id"] == "prq_fondeo":
            entry["search_scope"] = _search_scope(entry, margin)
            extended.append(entry["observation_id"])
    if len(extended) != 3:
        raise ValueError("Expected exactly three PRQ fondeo entries")
    manifest["schema_version"] = "blind-train-queue-v2"
    manifest["source_sha256"].update(
        parent_queue=PARENT_QUEUE_SHA256,
        proposal=PROPOSAL_SHA256,
        builder=_sha256(Path(__file__)),
    )
    manifest["policy"].update(
        sampling_purpose="Derived review of previously reviewed TRAIN; not a new blind sample or validation",
        search_scope_derivation={
            "parent_queue_id": parent["queue_id"],
            "previously_reviewed_train": True,
            "is_new_blind_sample": False,
            "is_validation": False,
            "radius_m": RADIUS_M,
            "extended_observation_ids": extended,
            "margin_is_official_zone": False,
            "reference_zone_unchanged": True,
            "scope_observability": "not_measured",
            "inherited_entry_reason": "Original selection rationale; not a new exposure assessment",
            "inherited_observable_fraction": "Reference zone only; not measured for the search margin",
        },
    )
    return {"queue_id": "train-" + digest(manifest)[:20], **manifest}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-queue", type=Path, default=ROOT / PARENT_QUEUE_RELATIVE)
    parser.add_argument("--proposal", type=Path, default=ROOT / PROPOSAL_RELATIVE)
    parser.add_argument("--output", type=Path, required=True,
                        help="New frozen queue file; existing files are never overwritten")
    args = parser.parse_args(argv)
    manifest = build_search_queue(args.parent_queue, args.proposal)
    checksum = freeze_queue(manifest, args.output)
    print(json.dumps({"queue_id": manifest["queue_id"], "sha256": checksum,
                      "entries": len(manifest["entries"]),
                      "extended_entries": sum("search_scope" in e for e in manifest["entries"]),
                      "activated": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
