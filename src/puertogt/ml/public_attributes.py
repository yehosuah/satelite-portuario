"""Import pinned public attribute references without inventing vessel outlines.

The selection schema is documented in reports/public-attributes/selection-v1.json.
Each sample keeps its original split, upstream window and label IDs, per-field
provenance, exact SAFE name and nine GCS object generations. The exported polygon
is an attribute context ROI, never a vessel extent or a presence annotation.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import tempfile
import urllib.parse
from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely.geometry import Point, Polygon, mapping
from shapely.ops import transform as project_geometry

from puertogt.contracts import ModelManifest, Prediction, RasterBundle, Scene
from puertogt.history import (
    HistoryLedger,
    HistoryLimits,
    _atomic_json,
    file_lock,
    materialize,
)
from puertogt.jobs import ResourceLimit
from puertogt.ml.features import FIXED_CONTEXT_V1, context_grid
from puertogt.preprocess import S2_BANDS
from puertogt.review import bundle_from_evidence
from puertogt.store import confined_path, sha256_file

PUBLIC_LIMITS = HistoryLimits(
    total_download_bytes=2 * 1024**3,
    acquisition_download_bytes=128 * 1024**2,
    volume_limit_bytes=4 * 1024**3,
    reserve_bytes=10 * 1024**3,
    io_workers=1,
)
SPLITS = {"sentinel2-attrs-train": "train", "sentinel2-attrs-val": "calibration",
          "sentinel2-attrs-test": "test"}


def load_selection(path: Path | str, expected_sha256: str) -> dict:
    path = Path(path)
    if sha256_file(path) != expected_sha256:
        raise ValueError("Public attribute selection checksum mismatch")
    selection = json.loads(path.read_text())
    if selection.get("schema_version") != "public-attributes-selection-v1" or not selection.get("samples"):
        raise ValueError("A frozen public attribute selection is required")
    seen, pass_splits = set(), {}
    metadata_hash = selection["metadata_source"]["sha256"]
    for sample in selection["samples"]:
        identity = sample["window_id"]
        if identity in seen:
            raise ValueError("Duplicate public attribute window")
        seen.add(identity)
        split = sample["import_split"]
        if SPLITS.get(sample["split"]) != split:
            raise ValueError("Public source splits cannot be reassigned")
        parts = sample["name"].split("_")
        expected_pass = ":".join([parts[0], parts[2], parts[4]])
        if sample["pass_key"] != expected_pass:
            raise ValueError("Public acquisition pass identity mismatch")
        if pass_splits.setdefault(expected_pass, split) != split:
            raise ValueError("Public acquisition pass appears across splits")
        if "public-attribute-pass:" + expected_pass not in sample["split_groups"]:
            raise ValueError("Every public sample requires its acquisition split group")
        for field, value in sample["attributes"].items():
            provenance = sample["attribute_provenance"].get(field, {})
            if (provenance.get("kind") != "public_dataset" or not provenance.get("reference")
                    or provenance.get("metadata_sha256") != metadata_hash):
                raise ValueError("Every public field requires pinned independent provenance")
            if field in {"length_m", "width_m"} and (
                    isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ValueError("Public dimensions must be positive finite metres")
        pinned = sample["raster_pin"]
        if set(pinned["bands"]) != set(S2_BANDS):
            raise ValueError("Exactly nine numeric Sentinel-2 bands are required")
        if not pinned.get("complete_exact_original") or pinned["requested_representation"] != sample["name"]:
            raise ValueError("The exact original SAFE representation must be pinned")
        for band, spec in pinned["bands"].items():
            if ("/" + sample["name"] + "/" not in spec["name"]
                    or "/IMG_DATA/" not in spec["name"] or not spec["name"].endswith("_"+band+".jp2")
                    or not str(spec["generation"]).isdigit() or int(spec["size"]) <= 0):
                raise ValueError("Band pin does not identify the original numeric source")
    return selection


def build_public_item(sample: dict, selection_sha256: str) -> tuple[dict, dict, dict]:
    if (sample["projection"].lower() != "epsg:3857" or sample["zoom"] != 13
            or sample["width"] != 128 or sample["height"] != 128):
        raise ValueError("Unsupported upstream attribute window grid")
    total_pixels = 512 * 2**sample["zoom"]
    resolution = 2 * math.pi * 6378137 / total_pixels
    column = sample["column"] + sample["width"] / 2
    row = sample["row"] + sample["height"] / 2
    x, y = (column-total_pixels/2)*resolution, -(row-total_pixels/2)*resolution
    lon, lat = Transformer.from_crs(3857, 4326, always_xy=True).transform(x, y)
    grid, crs = context_grid(mapping(Point(lon, lat)), FIXED_CONTEXT_V1)
    size = FIXED_CONTEXT_V1["size_px"]
    context = Polygon([grid * point for point in [(0, 0), (size, 0), (size, size), (0, size)]])
    to_wgs84 = Transformer.from_crs(crs, 4326, always_xy=True)
    reference = mapping(project_geometry(to_wgs84.transform, context))
    parts = sample["name"].split("_")
    captured = datetime.strptime(parts[2], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    identity = f"public-ai2-attribute-{sample['window_id']}"
    scene = Scene(scene_id=sample["name"], acquisition_key=identity, sensor="sentinel-2",
                  product_level="L1C", captured_at=captured, discovered_at=captured,
                  footprint=reference, provider="public-ai2-labels-and-gcs-sentinel2",
                  metadata={"source_product": sample["name"], "source_pass": sample["pass_key"],
                            "attribute_window_id": sample["window_id"],
                            "reference_center_provenance": "upstream_attribute_window_center",
                            "selection_sha256": selection_sha256})
    item = {"scene": scene.model_dump(mode="json"), "zones": [], "tiles": [{
        "tile_id": identity, "size": size, "crs": crs, "transform": list(grid)[:6], "zone_ids": [],
    }]}
    sources = {}
    for band, spec in sample["raster_pin"]["bands"].items():
        url = "https://storage.googleapis.com/gcp-public-data-sentinel-2/" + urllib.parse.quote(spec["name"], safe="/")
        sources[band] = {"url": url + "?generation=" + str(spec["generation"]), "size": int(spec["size"]),
                         "generation": str(spec["generation"]), "format": "jp2",
                         "md5Hash": spec.get("md5Hash"), "crc32c": spec.get("crc32c")}
    baseline = parts[3].removeprefix("N")
    discovery = {"source_product": sample["name"], "selection_sha256": selection_sha256,
                 "processing_baseline": baseline[:2]+"."+baseline[2:], "sources": sources}
    return item, discovery, reference


def _working_path(root: Path, item: dict) -> Path:
    key = item["scene"]["acquisition_key"]
    return root / "work" / hashlib.sha256(key.encode()).hexdigest()[:24]


def validate_performance_amendment(path, digest, selection_sha256):
    if not path or not digest or sha256_file(Path(path)) != digest:
        raise ValueError("A pinned performance amendment is required for concurrency changes")
    amendment = json.loads(Path(path).read_text())
    before = asdict(PUBLIC_LIMITS)
    if (amendment.get("schema_version") != "public-attributes-performance-amendment-v1"
            or amendment.get("selection_sha256") != selection_sha256
            or amendment.get("limits_before") != before
            or amendment.get("limits_after") != {**before, "io_workers": 4}
            or amendment.get("training_started") is not False
            or amendment.get("test_evaluation_started") is not False):
        raise ValueError("Performance amendment must preserve selection and every download budget")
    return amendment


def materialize_public_attributes(selection_path: Path | str, selection_sha256: str,
                                  destination: Path | str, *, max_samples: int | None = None,
                                  io_workers=1, performance_amendment=None, amendment_sha256=None) -> dict:
    """Sequential samples with bounded band I/O, no detector or catalog writes.

    The private transfer ledger lives in destination and never resets the main
    historical campaign. Existing pins and limits are immutable across resumes.
    """
    selection = load_selection(selection_path, selection_sha256)
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if max_samples is not None and max_samples < 1:
        raise ValueError("max_samples must be positive")
    if io_workers not in {1, 4}:
        raise ValueError("Public I/O concurrency must be one or explicitly amended to four")
    amendment = (validate_performance_amendment(performance_amendment, amendment_sha256, selection_sha256)
                 if io_workers == 4 else None)
    active_limits = replace(PUBLIC_LIMITS, io_workers=io_workers)
    with file_lock(root / "import.lock"):
        limits_path = root / "limits.json"
        before = json.loads(limits_path.read_text()) if limits_path.exists() else asdict(PUBLIC_LIMITS)
        if before != asdict(active_limits):
            if amendment is None or before != amendment["limits_before"]:
                raise ValueError("Public import download limits changed")
            receipt = root / "performance-amendments" / (amendment_sha256 + ".json")
            record = {"amendment_sha256": amendment_sha256, "selection_sha256": selection_sha256,
                      "limits_before": before, "limits_after": asdict(active_limits)}
            if receipt.exists() and json.loads(receipt.read_text()) != record:
                raise ValueError("Performance amendment receipt changed")
            _atomic_json(receipt, record)
            _atomic_json(limits_path, asdict(active_limits))
        ledger = HistoryLedger(root, active_limits)
        items = [build_public_item(sample, selection_sha256) for sample in selection["samples"]]
        plan = {"selection_sha256": selection_sha256, "feature_context": FIXED_CONTEXT_V1,
                "acquisitions": [item for item, _, _ in items]}
        if (root / "plan.json").exists():
            if json.loads((root / "plan.json").read_text()) != plan:
                raise ValueError("Public import plan changed; use a new destination")
        else:
            _atomic_json(root / "plan.json", plan)
        ledger.initialize_plan(plan)
        ledger.recover_interrupted()
        for item, discovery, _ in items[:max_samples]:
            key = item["scene"]["acquisition_key"]
            with closing(ledger.store.connect()) as connection:
                state = connection.execute("SELECT status FROM history_acquisitions WHERE acquisition_key=?", (key,)).fetchone()[0]
            if state == "complete":
                continue
            if ledger.claim(acquisition_key=key) is None:
                continue
            work = _working_path(root, item)
            work.mkdir(parents=True, exist_ok=True)
            sources_path = work / "sources.json"
            if sources_path.exists() and json.loads(sources_path.read_text()) != discovery:
                raise ValueError("Public source generations changed")
            _atomic_json(sources_path, discovery)
            try:
                _, receipt = materialize(item, ledger, quality_models=root / "unused-quality-models", quality=False)
                ledger.finish(key, receipt)
            except Exception as error:
                ledger.fail(key, error)
                if isinstance(error, ResourceLimit):
                    break
        result = {"selection_sha256": selection_sha256, "feature_context": FIXED_CONTEXT_V1,
                  "detector_predictions_computed": False, **ledger.summary()}
        _atomic_json(root / "materialization-status.json", result)
        return result


def finalize_public_attributes(selection_path: Path | str, selection_sha256: str,
                               destination: Path | str) -> Path:
    """Export portable per-field labels only after every fixed sample is ready."""
    selection = load_selection(selection_path, selection_sha256)
    root = Path(destination).resolve()
    target = root / "datasets"
    if target.exists():
        raise FileExistsError("Public dataset versions are immutable")
    with file_lock(root / "import.lock"):
        ledger = HistoryLedger(root, HistoryLimits(**json.loads((root / "limits.json").read_text())))
        if ledger.summary()["states"] != {"complete": len(selection["samples"])}:
            raise ValueError("Not all frozen public samples are complete")
        stage = Path(tempfile.mkdtemp(prefix=".datasets-pending-", dir=root))
        try:
            manifests = {split: {"version": "public-ai2-attributes-v1-"+selection_sha256[:12],
                                "split": split, "selection_sha256": selection_sha256,
                                "feature_context": FIXED_CONTEXT_V1,
                                "reference_scope": "public_attribute_contexts; not_local_validation",
                                "mmsi_disjointness": "unknown_not_published", "samples": []}
                         for split in ("train", "calibration", "test")}
            for sample in selection["samples"]:
                item, _, reference = build_public_item(sample, selection_sha256)
                identity = item["scene"]["acquisition_key"]
                prepared = _working_path(root, item) / identity / "prepared"
                bundle = RasterBundle.model_validate_json((prepared / "bundle.json").read_text())
                split = sample["import_split"]
                split_root = stage / split
                asset_root = split_root / "numeric" / identity
                asset_root.mkdir(parents=True)
                assets = []
                for name, source in {**bundle.band_paths, **bundle.mask_paths}.items():
                    expected = bundle.metadata["history_file_hashes"][name]
                    if sha256_file(source) != expected:
                        raise ValueError("Public numeric evidence checksum mismatch")
                    local = asset_root / (name+".tif")
                    shutil.copyfile(source, local)
                    descriptor = {"bands": [name] if name in bundle.band_paths else [],
                                  "masks": [name] if name in bundle.mask_paths else [],
                                  "units": {name: bundle.units[name]} if name in bundle.band_paths else {},
                                  "transform": bundle.transform, "sensor": bundle.sensor,
                                  "product_level": bundle.product_level,
                                  "preprocessing_version": bundle.preprocessing_version,
                                  "metadata": bundle.metadata}
                    assets.append({"path": local.relative_to(split_root).as_posix(), "sha256": expected,
                                   "metadata": {"crs": bundle.crs, "bundle": descriptor}})
                properties = {"id": identity, **sample["attributes"],
                              "provenance": sample["attribute_provenance"],
                              "geometry_role": "attribute_context_roi_not_vessel_extent",
                              "center_provenance": "upstream_attribute_window_center",
                              "mmsi": None, "upstream_label_id": sample["label_id"]}
                labels = split_root / (identity+".geojson")
                _atomic_json(labels, {"type": "FeatureCollection", "features": [{
                    "type": "Feature", "geometry": reference, "properties": properties,
                }]})
                manifests[split]["samples"].append({
                    "observation_id": identity, "acquisition_key": sample["name"],
                    "captured_at": item["scene"]["captured_at"], "split": split,
                    "split_groups": sample["split_groups"], "labels": labels.name, "sha256": sha256_file(labels),
                    "port_id": "external_public_dataset", "zone_id": "attribute_context", "sensor": "sentinel-2",
                    "evidence_assets": assets, "source_window_id": sample["window_id"],
                })
            for split, manifest in manifests.items():
                _atomic_json(stage / split / "manifest.json", manifest)
            stage.rename(target)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    return target


def cache_public_features(dataset: Path | str, encoder: ModelManifest, destination: Path | str,
                          *, heavy_lock: Path | str, threads: int = 4) -> Path:
    """Cache TRAIN features once with the same declared context as inference."""
    import torch

    from puertogt.ml.attributes import extract_features
    from puertogt.ml.detection import load_detector
    from puertogt.ml.training import validate_training_dataset

    source = Path(dataset).resolve()
    target = Path(destination).resolve()
    manifest = validate_training_dataset(source)
    if manifest.get("feature_context") != FIXED_CONTEXT_V1:
        raise ValueError("Public dataset context policy differs from the feature recipe")
    if target.exists():
        raise FileExistsError("Feature dataset versions are immutable")
    with file_lock(Path(heavy_lock)):
        torch.set_num_threads(threads)
        detector = load_detector(encoder)
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".features-pending-", dir=target.parent))
        try:
            shutil.copytree(source, stage, dirs_exist_ok=True)
            for sample in manifest["samples"]:
                labels = json.loads(confined_path(stage, sample["labels"]).read_text())
                detections = [Prediction(prediction_id=feature["properties"]["id"], geometry=feature["geometry"],
                                         detection_score=1) for feature in labels["features"]]
                encoded = extract_features(bundle_from_evidence(sample, stage), detections, encoder,
                                           detector=detector, context_policy=FIXED_CONTEXT_V1)
                cache = stage / (sample["observation_id"]+".features.npz")
                np.savez_compressed(cache, features=encoded,
                                    ids=np.asarray([item.prediction_id for item in detections], dtype=str))
                sample["feature_cache"] = {"path": cache.name, "sha256": sha256_file(cache),
                                           "encoder_sha256": encoder.weights_sha256, "pooling": "roi_mean",
                                           "feature_level": 0, "context_policy": FIXED_CONTEXT_V1}
            manifest.pop("manifest_sha256", None)
            manifest.pop("reserved_manifest_hashes", None)
            _atomic_json(stage / "manifest.json", manifest)
            stage.rename(target)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    return target / "manifest.json"
