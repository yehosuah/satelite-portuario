"""Derive bounded training crops without changing frozen labels or split identity."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import rasterio
from pyproj import Transformer
from rasterio.features import geometry_mask
from rasterio.windows import Window
from rasterio.windows import transform as window_transform
from shapely.geometry import Polygon, mapping, shape
from shapely.ops import transform as project
from shapely.ops import unary_union

from puertogt.ml.training import validate_training_dataset
from puertogt.ml.workflows import labels_for_sample, reviewed_area
from puertogt.review import bundle_from_evidence
from puertogt.store import confined_path, json_text, sha256_file, utc_text


def _polygonal_part(geometry):
    """Discard zero-area boundary contacts without expanding reviewed pixels."""
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry
    parts = [_polygonal_part(part) for part in getattr(geometry, "geoms", ())]
    return unary_union([part for part in parts if not part.is_empty]) if parts else Polygon()


def crop_training_dataset(source: Path, destination: Path, *, version: str,
                          size: int = 512, overlap: int = 64) -> dict:
    """Preserve numeric pixels, provenance and related groups; never repartition.

    Only train is derived. Calibration/test retain their original scene units.
    Known objects intersecting a crop are clipped as positive truncated objects;
    if their clipped center lies outside its reviewed scope, their whole visible
    footprint is excluded from that crop's negatives. Overlapping crops are not
    independent samples and retain the parent observation and acquisition groups.
    """
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if not version or not 32 <= size <= 512 or not 0 <= overlap < size:
        raise ValueError("Use a version, crop size32..512 and overlap smaller than size")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Derived dataset versions are immutable")
    dataset = validate_training_dataset(source)
    parent_hash = sha256_file(source / "manifest.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.pending-", dir=destination.parent))
    manifest = {"version": version, "split": "train", "format": dataset.get("format"),
                "created_at": utc_text(), "parent_manifest_sha256": parent_hash,
                "derivation": {"operation": "native_grid_crops", "size": size, "overlap": overlap,
                               "resampling": "none", "independent_samples": False}, "samples": []}

    def offsets(length):
        return sorted(set(range(0, max(1, length - size + 1), size - overlap)) | {max(0, length - size)})

    try:
        for sample in dataset["samples"]:
            bundle = bundle_from_evidence(sample, source)
            scope = reviewed_area(sample)
            if scope is None:
                raise ValueError("An explicit reviewed_area is required for local training crops")
            references = labels_for_sample(source, sample)
            numeric = [asset for asset in sample.get("evidence_assets", [])
                       if asset.get("metadata", {}).get("bundle")]
            with rasterio.open(next(iter(bundle.band_paths.values()))) as reference:
                height, width, grid, crs = reference.height, reference.width, reference.transform, reference.crs
            forward = Transformer.from_crs(crs, 4326, always_xy=True)
            backward = Transformer.from_crs(4326, crs, always_xy=True)
            # A WGS84 round trip can turn a boundary-only contact into a tiny
            # polygon. Discard at most 1e-8 of one pixel's area, not a real
            # truncated object; no buffers or coordinate shifts are applied.
            roundoff_area = abs(grid.a * grid.e - grid.b * grid.d) * 1e-8
            scope_native = project(backward.transform, scope)
            geometries = [(feature, project(backward.transform, shape(feature["geometry"])))
                          for feature in references]
            for y in offsets(height):
                for x in offsets(width):
                    window = Window(x, y, min(size, width - x), min(size, height - y))
                    cropped_grid = window_transform(window, grid)
                    footprint = Polygon([cropped_grid * p for p in
                                         [(0, 0), (window.width, 0), (window.width, window.height),
                                          (0, window.height), (0, 0)]])
                    area = _polygonal_part(scope_native.intersection(footprint))
                    if area.is_empty or area.area <= 0:
                        continue
                    labels = []
                    for feature, geometry in geometries:
                        visible = _polygonal_part(geometry.intersection(footprint))
                        if visible.is_empty or visible.area <= roundoff_area:
                            continue
                        if not area.covers(visible.centroid):
                            area = area.difference(visible)
                            continue
                        labels.append({**feature, "geometry": mapping(project(forward.transform, visible)),
                                       "properties": {**feature.get("properties", {}),
                                                      "clipped_to_training_crop": not footprint.covers(geometry)}})
                    if area.is_empty or area.area <= 0:
                        continue
                    # Projected edges curve slightly; keep final membership coherent.
                    area_wgs = _polygonal_part(project(forward.transform, area).intersection(scope))
                    while True:
                        excluded = [feature for feature in labels
                                    if not area_wgs.covers(shape(feature["geometry"]).centroid)]
                        if not excluded:
                            break
                        for feature in excluded:
                            area_wgs = _polygonal_part(area_wgs.difference(shape(feature["geometry"])))
                            labels.remove(feature)
                    if area_wgs.is_empty:
                        continue
                    inside = geometry_mask([project(backward.transform, area_wgs)],
                                           out_shape=(int(window.height), int(window.width)),
                                           transform=cropped_grid, invert=True)
                    if not inside.any():
                        continue
                    identity = f"{sample['observation_id']}:crop:{x}:{y}:{int(window.width)}:{int(window.height)}"
                    prefix = hashlib.sha256(identity.encode()).hexdigest()[:24]
                    evidence, hashes = [], {}
                    for index, asset in enumerate(numeric):
                        path = confined_path(source, asset["path"])
                        if sha256_file(path) != asset["sha256"]:
                            raise ValueError("Numeric evidence checksum mismatch")
                        relative = f"evidence/{prefix}-{index}.tif"
                        target = stage / relative
                        target.parent.mkdir(exist_ok=True)
                        with rasterio.open(path) as original:
                            if original.shape != (height, width) or original.crs != crs or not original.transform.almost_equals(grid):
                                raise ValueError("Training crop assets must share the frozen numeric grid")
                            profile = {**original.profile, "driver": "GTiff", "width": int(window.width),
                                       "height": int(window.height), "transform": cropped_grid,
                                       "compress": "deflate"}
                            with rasterio.open(target, "w", **profile) as output:
                                output.write(original.read(window=window))
                        metadata = copy.deepcopy(asset["metadata"])
                        descriptor = metadata["bundle"]
                        descriptor["transform"] = list(cropped_grid)[:6]
                        inherited = descriptor.get("metadata", {})
                        for field in ("sha256", "history_file_hashes", "wgs84_bounds", "observable_fraction",
                                      "potentially_observable_fraction", "cloud_fraction", "shadow_fraction"):
                            inherited.pop(field, None)
                        inherited.update(parent_manifest_sha256=parent_hash,
                                         parent_observation_id=sample["observation_id"],
                                         crop_window=[int(v) for v in (x, y, window.width, window.height)])
                        descriptor["metadata"] = inherited
                        metadata.update(bounds=list(footprint.bounds), wgs84_bounds=list(project(forward.transform, footprint).bounds),
                                        parent_asset_sha256=asset["sha256"])
                        digest = sha256_file(target)
                        hashes.update({band: digest for band in descriptor["bands"]})
                        evidence.append({"asset_id": f"{prefix}-{index}", "path": relative,
                                         "sha256": digest, "mime_type": "image/tiff", "metadata": metadata})
                    for asset in evidence:
                        asset["metadata"]["bundle"]["metadata"]["sha256"] = hashes
                    label_path = stage / f"{prefix}.geojson"
                    label_path.write_text(json_text({"type": "FeatureCollection", "features": labels}) + "\n")
                    manifest["samples"].append({
                        **sample, "observation_id": identity, "parent_observation_id": sample["observation_id"],
                        "split_groups": sorted({sample["acquisition_key"], f"observation:{sample['observation_id']}",
                                                 *sample.get("split_groups", [])}),
                        "reviewed_area": mapping(area_wgs), "crop_window": [int(x), int(y), int(window.width), int(window.height)],
                        "reviewed_pixel_count": int(inside.sum()),
                        "labels": label_path.name, "sha256": sha256_file(label_path), "evidence_assets": evidence,
                    })
        if not manifest["samples"]:
            raise ValueError("No reviewed pixels overlap a training crop")
        (stage / "manifest.json").write_text(json_text(manifest) + "\n")
        validate_training_dataset(stage)
        for sample in manifest["samples"]:
            labels_for_sample(stage, sample)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Derived dataset versions are immutable")
        stage.rename(destination)
        return manifest
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    result = crop_training_dataset(args.source, args.destination, version=args.version)
    print(json.dumps({"samples": len(result["samples"]), "destination": str(args.destination)}))
