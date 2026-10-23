"""OSCD research adapter: image coordinates only, never fabricated georeferencing.

The official rectified TIFFs and manual masks have no CRS or affine transform.
This format shares the learned change network with the geographic workflow, but
its independent evaluation reports pixel segmentation, not mapped port events.
The archive README's noncommercial label license is retained despite conflicting
DOI metadata. Neither weights nor masks are implicitly registered or published.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import zipfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.io import MemoryFile

from puertogt.preprocess import S2_BANDS
from puertogt.store import confined_path, json_text, sha256_file, utc_text

PIXEL_PAIR_FORMAT = "puertogt-public-change-pixels-v1"


def decode_manual_mask(tiff_values: np.ndarray, png_values: np.ndarray) -> np.ndarray:
    """Verify the archive's 1/2 TIFF encoding against its documented 0/255 PNG.

    The shipped TIFFs contradict the README's stated 0/1 encoding. Accept a
    mapping only when every pixel agrees with the companion visualization.
    """
    if tiff_values.shape != png_values.shape or not np.isin(png_values, (0, 255)).all():
        raise ValueError("OSCD annotation visualization is not a matching binary grid")
    if np.isin(tiff_values, (1, 2)).all():
        labels = (tiff_values - 1).astype("uint8")
    elif np.isin(tiff_values, (0, 1)).all():
        labels = tiff_values.astype("uint8")
    else:
        raise ValueError("Unsupported OSCD label encoding")
    if not np.array_equal(labels, (png_values == 255).astype("uint8")):
        raise ValueError("OSCD TIFF and documented PNG label semantics disagree")
    return labels


def measure_alignment(before: np.ndarray, after: np.ndarray) -> dict:
    """Measure unchanged local gradient displacement, without labels or warping.

    A 4x4 grid uses fixed 32px patches, +/-8px integer search, NCC>=0.6 and
    >=0.05 separation from the best competitor outside the adjacent pixel.
    Four stable patches are required. This diagnostic does not certify subpixel
    accuracy; it deliberately reports the uncorrected displacement RMS.
    """
    if before.ndim != 2 or before.shape != after.shape or min(before.shape) < 96:
        raise ValueError("Alignment needs equal image grids of at least 96 pixels")
    arrays = []
    for image in (before, after):
        if not np.isfinite(image).all():
            raise ValueError("Alignment inputs must be finite")
        gy, gx = np.gradient(image.astype("float64"))
        arrays.append(np.hypot(gy, gx))
    left, right = arrays
    records = []
    for row in np.linspace(24, before.shape[0] - 25, 4).astype(int):
        for col in np.linspace(24, before.shape[1] - 25, 4).astype(int):
            patch = left[row - 16 : row + 16, col - 16 : col + 16].copy()
            patch -= patch.mean()
            norm = np.linalg.norm(patch)
            if norm < 1e-8:
                continue
            scores = []
            for dy in range(-8, 9):
                for dx in range(-8, 9):
                    candidate = right[row - 16 + dy : row + 16 + dy, col - 16 + dx : col + 16 + dx]
                    centered = candidate - candidate.mean()
                    denominator = norm * np.linalg.norm(centered)
                    ncc = float((patch * centered).sum() / denominator) if denominator > 0 else -1
                    scores.append((ncc, dy, dx))
            scores.sort(reverse=True)
            best = scores[0]
            competitor = next(
                value[0]
                for value in scores
                if max(abs(value[1] - best[1]), abs(value[2] - best[2])) > 1
            )
            if best[0] >= 0.6 and best[0] - competitor >= 0.05:
                records.append(
                    {"row": int(row), "col": int(col), "dy": best[1], "dx": best[2], "ncc": best[0]}
                )
    return {
        "method": "gradient_patch_ncc_integer_v1",
        "stable_patches": len(records),
        "rmse_pixels": math.sqrt(np.mean([v["dx"] ** 2 + v["dy"] ** 2 for v in records]))
        if len(records) >= 4
        else None,
        "patches": records,
        "subpixel_accuracy_certified": False,
    }


def load_pixel_pair(root: Path, sample: dict, protocol: dict):
    """Verify hash, shape, availability and explicit public annotation provenance."""
    from puertogt.ml.change_workflows import _label_provenance

    _label_provenance(sample["labels"], require_kind=True)
    if sample["labels"].get("review_kind") != "public_dataset":
        raise ValueError("Pixel-only pairs require explicit public dataset provenance")
    if sample.get("sensor") != "sentinel-2" or utc_text(
        sample["reference"]["captured_at"]
    ) >= utc_text(sample["target"]["captured_at"]):
        raise ValueError("Public change pairs require chronological optical captures")
    descriptor = sample["numeric_pair"]
    path = confined_path(root, descriptor["path"])
    if sha256_file(path) != descriptor["sha256"]:
        raise ValueError("Public pair checksum mismatch")
    quality = sample.get("quality", {})
    error = quality.get("alignment_rmse_pixels")
    if (
        error is None
        or not math.isfinite(error)
        or error < 0
        or error > protocol["max_alignment_rmse_pixels"]
        or quality.get("radiometric_comparable") is not True
        or quality.get("coordinate_system") != "source_pixel_grid"
    ):
        raise ValueError("Public pair alignment/radiometry provenance is insufficient")
    with np.load(path, allow_pickle=False) as values:
        left, right, labels, valid = (values[key] for key in ("before", "after", "labels", "valid"))
    if (
        left.ndim != 3
        or left.shape[0] != 9
        or left.shape != right.shape
        or max(left.shape[-2:]) > 512
        or labels.shape != left.shape[-2:]
        or valid.shape != labels.shape
        or not np.isin(valid, (0, 1)).all()
        or not np.isfinite(left).all()
        or not np.isfinite(right).all()
        or min(left.min(), right.min()) < 0
    ):
        raise ValueError("Invalid bounded public numeric pair grid")
    valid = valid.astype(bool)
    if sample.get("spatial_window") is not None:
        _, _, width, height = sample["spatial_window"]
        if labels.shape != (height + 64, width + 64):
            raise ValueError("Public pair window and fixed 32-pixel halo disagree")
        if valid[:32].any() or valid[-32:].any() or valid[:, :32].any() or valid[:, -32:].any():
            raise ValueError("Public supervision must exclude every halo and padded pixel")
    if not valid.any() or not np.isin(labels[valid], (0, 1)).all():
        raise ValueError("Public masks need observable binary reference pixels")
    if bool((labels[valid] == 1).any()) == sample["labels"]["negative"]:
        raise ValueError("Public negative flag disagrees with observable labels")
    divisor = np.asarray([3000] * 3 + [8160] * 6, dtype="float32")[:, None, None]
    return (
        np.clip(left.astype("float32") / divisor, 0, 1),
        np.clip(right.astype("float32") / divisor, 0, 1),
        labels,
        valid,
    )


def _pad_crop(values: np.ndarray, x: int, y: int, width: int, height: int, halo: int):
    height_full, width_full = values.shape[-2:]
    result = np.zeros((*values.shape[:-2], height + 2 * halo, width + 2 * halo), dtype=values.dtype)
    sx, sy, ex, ey = (
        max(0, x - halo),
        max(0, y - halo),
        min(width_full, x + width + halo),
        min(height_full, y + height + halo),
    )
    result[..., sy - y + halo : ey - y + halo, sx - x + halo : ex - x + halo] = values[
        ..., sy:ey, sx:ex
    ]
    return result


def prepare_oscd(
    source_root: Path, train_labels: Path, test_labels: Path, destination: Path, protocol_path: Path
) -> dict:
    """Materialize every fixed core in selected cities; record every quality exclusion."""
    if destination.exists():
        raise FileExistsError("Prepared public datasets are immutable")
    protocol = json.loads(protocol_path.read_text())
    source_manifest = json.loads((source_root.parent / "source-manifest.json").read_text())
    hashes = {row["path"]: row["sha256"] for row in source_manifest["files"]}
    if set(protocol["train"]) & set(protocol["test"]):
        raise ValueError("OSCD train/test cities overlap")
    from shapely.geometry import shape

    footprints = {}
    for city in protocol["train"] + protocol["test"]:
        relative = f"{city}/{city}.geojson"
        path = confined_path(source_root, relative)
        if sha256_file(path) != hashes[relative]:
            raise ValueError("Official source footprint hash mismatch")
        footprints[city] = shape(json.loads(path.read_text())["features"][0]["geometry"])
    if any(
        footprints[left].intersects(footprints[right])
        for left in protocol["train"]
        for right in protocol["test"]
    ):
        raise ValueError("OSCD train/test geographic footprints overlap")
    core, halo = 256, 32
    report = {"protocol_sha256": sha256_file(protocol_path), "cities": [], "exclusions": []}
    destination.mkdir(parents=True)
    for split, label_archive in (("train", train_labels), ("test", test_labels)):
        if sha256_file(label_archive) != source_manifest["labels_archives"][split]["sha256"]:
            raise ValueError("Official source annotation archive hash mismatch")
        root = destination / split
        root.mkdir()
        samples = []
        with zipfile.ZipFile(label_archive) as archive:
            for city in protocol[split]:
                images = []
                for role in (1, 2):
                    bands = []
                    for band in S2_BANDS:
                        relative = f"{city}/imgs_{role}_rect/{band}.tif"
                        path = confined_path(source_root, relative)
                        if sha256_file(path) != hashes[relative]:
                            raise ValueError("Official source image hash mismatch")
                        with rasterio.open(path) as source:
                            if source.count != 1 or source.crs is not None:
                                raise ValueError(
                                    "Expected the official unreferenced single-band OSCD grid"
                                )
                            bands.append(source.read(1))
                    images.append(np.stack(bands))
                before, after = images
                if before.shape != after.shape:
                    raise ValueError("OSCD paired grid mismatch")
                date_path = source_root / city / "dates.txt"
                if sha256_file(date_path) != hashes[f"{city}/dates.txt"]:
                    raise ValueError("Official source date hash mismatch")
                dates = re.findall(r"date_[12]:\s*(\d{8})", date_path.read_text())
                if len(dates) != 2 or dates[0] >= dates[1] or dates[1] >= "20220125":
                    raise ValueError("Expected chronological pre-offset Sentinel-2 L1C dates")
                alignment = measure_alignment(before[0], after[0])
                record = {
                    "city": city,
                    "split": split,
                    "shape": list(before.shape),
                    "dates": dates,
                    "alignment": alignment,
                }
                report["cities"].append(record)
                error = alignment["rmse_pixels"]
                if error is None or error > protocol["training"]["max_alignment_rmse_pixels"]:
                    report["exclusions"].append(
                        {"city": city, "reason": "alignment_gate", "evidence": alignment}
                    )
                    continue
                # Public annotations are read only after source-only quality is measured.
                label_name = next(
                    name
                    for name in archive.namelist()
                    if name.endswith(f"/{city}/cm/{city}-cm.tif")
                )
                label_bytes = archive.read(label_name)
                with MemoryFile(label_bytes) as memory, memory.open() as source:
                    tiff_values = source.read(1)
                png_bytes = archive.read(label_name.rsplit("/", 1)[0] + "/cm.png")
                with MemoryFile(png_bytes) as memory, memory.open() as source:
                    png_values = source.read(1)
                labels = decode_manual_mask(tiff_values, png_values)
                if labels.shape != before.shape[-2:] or not np.isin(labels, (0, 1)).all():
                    raise ValueError("OSCD manual labels disagree with their source pixel grid")
                available = (before > 0).all(axis=0) & (after > 0).all(axis=0)
                land = np.ones(labels.shape, dtype=bool)
                for image in images:
                    green, nir = image[1].astype("float32"), image[6].astype("float32")
                    land &= (green - nir) / np.maximum(green + nir, 1) <= 0.2
                support = available & land
                h, w = labels.shape
                for y in range(0, h, core):
                    for x in range(0, w, core):
                        width, height = min(core, w - x), min(core, h - y)
                        valid = _pad_crop(support, x, y, width, height, halo)
                        valid[:halo] = False
                        valid[-halo:] = False
                        valid[:, :halo] = False
                        valid[:, -halo:] = False
                        if not valid.any():
                            report["exclusions"].append(
                                {
                                    "city": city,
                                    "window": [x, y, width, height],
                                    "reason": "no_available_land",
                                }
                            )
                            continue
                        values = _pad_crop(labels, x, y, width, height, halo)
                        name = f"{city}-{y:04d}-{x:04d}"
                        path = root / f"{name}.npz"
                        np.savez_compressed(
                            path,
                            before=_pad_crop(before, x, y, width, height, halo),
                            after=_pad_crop(after, x, y, width, height, halo),
                            labels=values,
                            valid=valid,
                        )
                        roles = {}
                        for index, (role, date) in enumerate(
                            zip(("reference", "target"), dates), 1
                        ):
                            original_name = source_manifest["original_band_names"][city][str(index)]
                            tile = re.search(r"T\d{2}[A-Z]{3}", original_name)
                            if tile is None:
                                raise ValueError("Original Sentinel-2 tile identity missing")
                            roles[role] = {
                                "observation_id": f"oscd:{city}:{date}",
                                "acquisition_key": f"S2:{date}:{tile.group(0)}",
                                "captured_at": f"{date[:4]}-{date[4:6]}-{date[6:]}T00:00:00Z",
                                "timestamp_precision": "day",
                                "original_source_filename": original_name,
                            }
                        samples.append(
                            {
                                "pair_id": name,
                                "split_groups": [f"oscd-city:{city}"],
                                "spatial_window": [x, y, width, height],
                                "split": split,
                                "port_id": f"public-oscd:{city}",
                                "zone_id": "urban_land",
                                "sensor": "sentinel-2",
                                **roles,
                                "numeric_pair": {"path": path.name, "sha256": sha256_file(path)},
                                "quality": {
                                    "coordinate_system": "source_pixel_grid",
                                    "radiometric_comparable": True,
                                    "alignment_rmse_pixels": error,
                                    "cloud_screening": "not_independently_validated",
                                    "nominal_pixel_resolution_m": 10,
                                },
                                "labels": {
                                    "review_kind": "public_dataset",
                                    "complete": True,
                                    "negative": not bool((values[valid] == 1).any()),
                                    "provenance": {
                                        "dataset": "OSCD",
                                        "source": "https://doi.org/10.21227/asqe-7s69",
                                        "annotation_method": "manual_pixel_annotation",
                                        "attribution": "Daudt, Le Saux, Boulch, Gousseau (2018)",
                                        "source_sha256": hashlib.sha256(label_bytes).hexdigest(),
                                        "license": "CC-BY-NC-SA per official README; conflicting DOI CC-BY retained",
                                    },
                                },
                            }
                        )
                record["included_cores"] = sum(
                    row["split_groups"] == [f"oscd-city:{city}"] for row in samples
                )
        manifest = {
            "format": PIXEL_PAIR_FORMAT,
            "version": "oscd-bounded-v1",
            "split": split,
            "frozen": True,
            "protocol_sha256": report["protocol_sha256"],
            "source_manifest_sha256": sha256_file(source_root.parent / "source-manifest.json"),
            "quality_protocol": {
                "max_alignment_rmse_pixels": protocol["training"]["max_alignment_rmse_pixels"],
                "minimum_observable_fraction": 0.5,
            },
            "samples": samples,
            "scope": "public image-coordinate subset; no local geographic/persistence validation",
        }
        (root / "manifest.json").write_text(json_text(manifest))
    (destination / "preparation.json").write_text(json_text(report))
    return report


def evaluate_pixel_dataset(root: Path, model, dataset: dict) -> dict:
    """Evaluate a preflight-verified test manifest once, with fixed model policy."""
    import torch
    from rasterio.features import rasterize, shapes
    from shapely.geometry import shape

    from puertogt.ml.change import load_change_model

    threshold = model.publication_policy["change_threshold"]
    minimum_area = model.publication_policy.get("min_area_m2")
    if minimum_area is None or minimum_area <= 0:
        raise ValueError("Public evaluation requires a fixed minimum nominal area")
    network = load_change_model(model)
    records = []
    for sample in dataset["samples"]:
        before, after, labels, valid = load_pixel_pair(root, sample, dataset["quality_protocol"])
        height, width = labels.shape
        pad = ((0, (-height) % 32), (0, (-width) % 32))
        with torch.inference_mode():
            inputs = [
                torch.from_numpy(np.pad(image, ((0, 0), *pad)))[None] for image in (before, after)
            ]
            probabilities = torch.sigmoid(network(*inputs))[0, 0, :height, :width].numpy()
        if not np.isfinite(probabilities[valid]).all():
            raise ValueError("Non-finite public change prediction")
        predicted = (probabilities >= threshold) & valid
        polygons = [
            geometry
            for geometry, value in shapes(predicted.astype("uint8"), mask=predicted)
            if value == 1 and shape(geometry).area * 100 >= minimum_area
        ]
        predicted = (
            rasterize(
                ((polygon, 1) for polygon in polygons), out_shape=valid.shape, dtype="uint8"
            ).astype(bool)
            if polygons
            else np.zeros(valid.shape, dtype=bool)
        )
        predicted &= valid
        truth = (labels == 1) & valid
        records.append(
            {
                "pair_id": sample["pair_id"],
                "city": sample["split_groups"][0],
                "tp": int((predicted & truth).sum()),
                "fp": int((predicted & ~truth).sum()),
                "fn": int((~predicted & truth).sum()),
                "tn": int((~predicted & ~truth & valid).sum()),
                "valid_pixels": int(valid.sum()),
                "candidate_components": len(polygons),
                "negative": sample["labels"]["negative"],
            }
        )

    def summary(rows):
        counts = {
            key: sum(row[key] for row in rows) for key in ("tp", "fp", "fn", "tn", "valid_pixels")
        }
        tp, fp, fn = (counts[key] for key in ("tp", "fp", "fn"))
        return {
            **counts,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
            "iou": tp / (tp + fp + fn) if tp + fp + fn else None,
            "negative_cores": sum(row["negative"] for row in rows),
            "negative_cores_with_false_alarm": sum(
                row["negative"] and row["fp"] > 0 for row in rows
            ),
        }

    return {
        "schema": "public-change-pixel-evaluation-v1",
        "model_id": model.model_id,
        "weights_sha256": model.weights_sha256,
        "test_manifest_sha256": dataset["manifest_sha256"],
        "threshold": threshold,
        "minimum_nominal_area_m2": minimum_area,
        "total": summary(records),
        "by_city": {
            city: summary([row for row in records if row["city"] == city])
            for city in sorted({row["city"] for row in records})
        },
        "cores": records,
        "scope": "OSCD fixed public pixel subset; no local port, geographic accuracy or persistence validation",
        "local_validation": False,
        "automatic_publication": False,
        "limitations": [
            "No source CRS/affine; no geographic polygons produced",
            "Cloud screening is not independently validated",
            "Alignment diagnostic is integer-pixel and source-quality conditioned",
            "Noncommercial label restriction and DOI license conflict retained",
        ],
    }
