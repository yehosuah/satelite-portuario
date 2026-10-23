"""Reviewed optical pairs, supervised change training and audited temporal evidence.

Training consumes ``manifest.json`` with this exact versioned structure::

    {"format": "puertogt-reviewed-change-pairs-v1", "version": "pairs-v1",
     "split": "train", "quality_protocol": {
       "max_alignment_rmse_pixels": 0.5, "minimum_observable_fraction": 0.9},
     "samples": [{"pair_id": "pair-1", "split_groups": ["overlapping-series-1"],
       "port_id": "prq", "zone_id": "prq_patios", "sensor": "sentinel-2",
       "reference": {"bundle": "t0.json", "sha256": "<64 hex>",
         "observation_id": "t0", "acquisition_key": "take-0",
         "captured_at": "2025-01-01T00:00:00Z"},
       "target": {"bundle": "t1.json", "sha256": "<64 hex>",
         "observation_id": "t1", "acquisition_key": "take-1",
         "captured_at": "2025-01-11T00:00:00Z"},
       "labels": {"path": "pair-1.tif", "sha256": "<64 hex>",
         "review_kind": "human", "reviewed_by": "operator", "reviewed_at": "2026-01-01T00:00:00Z",
         "complete": true, "negative": false},
       "asset_hashes": {"t0-bands.tif": "<64 hex>", "t1-bands.tif": "<64 hex>",
         "t0-observable.tif": "<64 hex>", "t1-observable.tif": "<64 hex>",
         "static.tif": "<64 hex>"}}]}

The numbers above illustrate the schema, not measured operating thresholds. Every
bundle JSON is a RasterBundle with paths relative to the dataset root. Every band
and mask file must appear in that sample's asset_hashes. Labels are a georeferenced
single-band 0/1 raster on the common grid; nodata excludes pixels from the loss.
Both bundles need observable and static_area masks; vessels and temporary masks,
when present, exclude transient objects. Target metadata must carry measured
alignment_rmse_pixels and radiometric_comparable=true. A training dataset needs
reviewed positive and fully negative pairs. Shared acquisition IDs and declared
overlap/series/identity groups must remain in one split. Reserved manifests use
the same structure and split=calibration or test. No automatic proposals become
reference labels in this module, and training never grants publication approval.
Test evaluation additionally requires frozen=true, the three stratum identifiers
on every pair, and review_kind=human on each reference mask.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import rasterio
from rasterio import Affine

from puertogt.contracts import ChangeEvent, ModelManifest, RasterBundle
from puertogt.ml.change import (
    TemporalEvidence,
    compare,
    create_satlas_change_model,
    masked_change_loss,
    track_change,
)
from puertogt.ml.training import export_training_artifact
from puertogt.models import register_portable_model
from puertogt.preprocess import read_model_input
from puertogt.store import Store, confined_path, json_text, sha256_file, utc_text

PAIR_FORMAT = "puertogt-reviewed-change-pairs-v1"


def _pair_identity(sample: dict) -> tuple:
    """Windows retain the real acquisition identity and its split membership."""
    pair = tuple(sample[role].get("acquisition_key") for role in ("reference", "target"))
    window = sample.get("spatial_window")
    if window is None:
        return (*pair, None)
    if (not isinstance(window, list) or len(window) != 4
            or any(type(value) is not int for value in window)
            or min(window[:2]) < 0 or min(window[2:]) <= 0):
        raise ValueError("Pair spatial_window must be nonnegative x/y and positive width/height")
    return (*pair, tuple(window))


def _label_provenance(label: dict, *, require_kind: bool = False) -> None:
    if label.get("complete") is not True or type(label.get("negative")) is not bool:
        raise ValueError("Pairs require complete annotation and explicit negative labels")
    kind = label.get("review_kind", None if require_kind else "human")
    if kind == "public_dataset":
        provenance = label.get("provenance", {})
        if (provenance.get("annotation_method") != "manual_pixel_annotation"
                or not all(isinstance(provenance.get(field), str) and provenance[field].strip()
                           for field in ("dataset", "source", "attribution", "license"))
                or not re.fullmatch(r"[a-f0-9]{64}", str(provenance.get("source_sha256", "")))):
            raise ValueError("Public manual labels require complete source/license provenance")
    elif kind == "human":
        if not str(label.get("reviewed_by", "")).strip() or not label.get("reviewed_at"):
            raise ValueError("Pairs require complete, attributed human review")
        utc_text(label["reviewed_at"])
    else:
        raise ValueError("References require human review or attributed public manual provenance")


def _verified(root: Path, path: str, expected: str) -> Path:
    source = confined_path(root, path)
    if not expected or sha256_file(source) != expected:
        raise ValueError(f"Dataset checksum mismatch: {path}")
    return source


def _groups(sample: dict) -> set[str]:
    declared = sample.get("split_groups")
    if (
        not isinstance(declared, list)
        or not declared
        or not all(isinstance(v, str) and v for v in declared)
    ):
        raise ValueError("Every pair needs explicit overlap/temporal split groups")
    keys = [sample[role].get("acquisition_key") for role in ("reference", "target")]
    ids = [sample[role].get("observation_id") for role in ("reference", "target")]
    if not all(keys) or not all(ids) or keys[0] == keys[1] or ids[0] == ids[1]:
        raise ValueError("A pair requires distinct observations and acquisition identities")
    return {*declared, *(f"acquisition:{key}" for key in keys), *(f"observation:{v}" for v in ids)}


def _dataset(root: Path, reserved_manifests: Iterable[Path]) -> dict:
    from puertogt.ml.oscd import PIXEL_PAIR_FORMAT
    path = confined_path(root, "manifest.json")
    manifest = json.loads(path.read_text())
    if manifest.get("format") not in {PAIR_FORMAT, PIXEL_PAIR_FORMAT} or manifest.get("split") != "train":
        raise ValueError("Change training requires the reviewed pairs format and train split")
    if not manifest.get("version") or not manifest.get("samples"):
        raise ValueError("A versioned nonempty pair dataset is required")
    groups, identifiers, pairs = set(), set(), set()
    for sample in manifest["samples"]:
        if ("numeric_pair" in sample) != (manifest["format"] == PIXEL_PAIR_FORMAT):
            raise ValueError("Numeric pair representation must match the dataset format")
        identifier = sample.get("pair_id")
        pair = _pair_identity(sample)
        if not identifier or identifier in identifiers or pair in pairs:
            raise ValueError("Pair IDs and acquisition pairs must be unique")
        if sample.get("split", "train") != "train":
            raise ValueError("Every training pair must belong to train")
        identifiers.add(identifier)
        pairs.add(pair)
        groups.update(_groups(sample))
    reserved_hashes = []
    for reserved_path in reserved_manifests:
        reserved_path = Path(reserved_path)
        reserved = json.loads(reserved_path.read_text())
        if (
            reserved.get("format") != manifest["format"]
            or reserved.get("split") not in {"calibration", "test"}
            or not reserved.get("samples")
        ):
            raise ValueError("Reserved pairs must be nonempty calibration/test manifests")
        if any(groups.intersection(_groups(sample)) for sample in reserved["samples"]):
            raise ValueError(
                "Split leakage: training and reserved pairs contain related acquisitions or groups"
            )
        reserved_hashes.append(sha256_file(reserved_path))
    return {
        **manifest,
        "manifest_sha256": sha256_file(path),
        "reserved_manifest_hashes": reserved_hashes,
    }


def _load_bundle(root: Path, descriptor: dict, hashes: dict) -> RasterBundle:
    path = _verified(root, descriptor["bundle"], descriptor["sha256"])
    value = json.loads(path.read_text())
    for field in ("band_paths", "mask_paths"):
        value[field] = {
            name: str(_verified(root, source, hashes.get(source)))
            for name, source in value.get(field, {}).items()
        }
    bundle = RasterBundle.model_validate(value)
    if bundle.observation_id != descriptor["observation_id"] or utc_text(
        bundle.metadata.get("captured_at")
    ) != utc_text(descriptor["captured_at"]):
        raise ValueError("Pair descriptors and numeric bundle identity/timestamps disagree")
    return bundle


def _grid_raster(
    path: Path, bundle: RasterBundle, shape: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray]:
    with rasterio.open(path) as source:
        if (
            source.count != 1
            or source.shape != shape
            or str(source.crs) != bundle.crs
            or not source.transform.almost_equals(Affine(*bundle.transform))
        ):
            raise ValueError("Change masks and labels must share the numeric grid")
        values = source.read(1)
        valid = source.read_masks(1) > 0
    if not np.isfinite(values[valid]).all():
        raise ValueError("Change masks and labels require finite values")
    return values, valid


def _mask(bundle: RasterBundle, name: str, shape: tuple[int, int]) -> np.ndarray:
    if name not in bundle.mask_paths:
        raise ValueError(f"Change evidence requires the {name} mask")
    values, available = _grid_raster(bundle.mask_paths[name], bundle, shape)
    if not np.isin(values, (0, 1)).all():
        raise ValueError("Change masks must be binary")
    return (values > 0) & available


def _quality(protocol: dict, target: RasterBundle) -> None:
    maximum = protocol.get("max_alignment_rmse_pixels")
    minimum = protocol.get("minimum_observable_fraction")
    error = target.metadata.get("alignment_rmse_pixels")
    if (
        maximum is None
        or minimum is None
        or not math.isfinite(maximum)
        or maximum < 0
        or not math.isfinite(minimum)
        or not 0 < minimum <= 1
    ):
        raise ValueError("Explicit alignment and observability thresholds are required")
    if (
        error is None
        or not math.isfinite(error)
        or error < 0
        or error > maximum
        or target.metadata.get("radiometric_comparable") is not True
    ):
        raise ValueError("Change pair is not demonstrably aligned and radiometrically comparable")


def _pair_arrays(reference: RasterBundle, target: RasterBundle, protocol: dict):
    _quality(protocol, target)
    if (
        reference.sensor != "sentinel-2"
        or target.sensor != "sentinel-2"
        or reference.crs != target.crs
        or not np.allclose(reference.transform, target.transform, rtol=0, atol=1e-9)
    ):
        raise ValueError("Change pairs require one coregistered optical grid")
    if utc_text(reference.metadata.get("captured_at")) >= utc_text(
        target.metadata.get("captured_at")
    ):
        raise ValueError("Change target must follow its reference")
    before, after = read_model_input(reference), read_model_input(target)
    if before.shape != after.shape or max(before.shape[-2:]) > 512:
        raise ValueError("Pairs must share a grid bounded to 512 pixels per side")
    shape = before.shape[-2:]
    static = _mask(reference, "static_area", shape) & _mask(target, "static_area", shape)
    valid = static & _mask(reference, "observable", shape) & _mask(target, "observable", shape)
    for bundle in (reference, target):
        for name in ("vessels", "temporary"):
            if name in bundle.mask_paths:
                valid &= ~_mask(bundle, name, shape)
    if not static.any() or valid.sum() / static.sum() < protocol["minimum_observable_fraction"]:
        raise ValueError("Insufficient observable static area after excluding transient objects")
    return before, after, valid


def _reviewed_pair(root: Path, sample: dict, protocol: dict):
    if "numeric_pair" in sample:
        from puertogt.ml.oscd import load_pixel_pair
        return load_pixel_pair(root, sample, protocol)
    label = sample["labels"]
    _label_provenance(label)
    before = _load_bundle(root, sample["reference"], sample["asset_hashes"])
    after = _load_bundle(root, sample["target"], sample["asset_hashes"])
    if sample.get("spatial_window") is not None:
        _pair_identity(sample)
        if any(bundle.metadata.get("source_spatial_window") != sample["spatial_window"]
               for bundle in (before, after)):
            raise ValueError("Pair spatial window disagrees with its numeric evidence")
    left, right, valid = _pair_arrays(before, after, protocol)
    path = _verified(root, label["path"], label["sha256"])
    values, label_valid = _grid_raster(path, after, valid.shape)
    valid &= label_valid
    if not valid.any() or not np.isin(values[valid], (0, 1)).all():
        raise ValueError("Reviewed labels must contain observable binary pixels")
    has_change = bool((values[valid] == 1).any())
    if has_change == label["negative"]:
        raise ValueError("Explicit negative/positive pair label disagrees with its observable mask")
    return left, right, values, valid


def train_change_dataset(
    dataset_dir: Path | str,
    base_manifest: ModelManifest,
    destination: Path | str,
    *,
    epochs: int = 1,
    learning_rate: float = 1e-4,
    device: str = "cpu",
    reserved_manifests: Iterable[Path] = (),
    positive_weight: float = 1.0,
    max_seconds: float | None = None,
) -> ModelManifest:
    """Train the own decoder on reviewed pairs; save full encoder/decoder CPU weights."""
    if (
        base_manifest.task != "change"
        or base_manifest.sensor != "sentinel-2"
        or base_manifest.metadata.get("architecture") != "satlas_swint_siamese_unet_v1"
    ):
        raise ValueError("A pinned optical Satlas change recipe is required")
    if (
        isinstance(epochs, bool)
        or not isinstance(epochs, int)
        or epochs < 1
        or not math.isfinite(learning_rate)
        or learning_rate <= 0
        or not math.isfinite(positive_weight)
        or positive_weight <= 0
    ):
        raise ValueError("Training epochs and learning rate must be positive")
    if device not in {"cpu", "mps"}:
        raise ValueError("Change training supports CPU or MPS")
    if max_seconds is not None and (not math.isfinite(max_seconds) or max_seconds <= 0):
        raise ValueError("Training time budget must be finite and positive")
    root = Path(dataset_dir).resolve()
    dataset = _dataset(root, reserved_manifests)
    positive, negative = 0, 0
    for sample in dataset["samples"]:
        _, _, values, valid = _reviewed_pair(root, sample, dataset.get("quality_protocol", {}))
        has_change = bool((values[valid] == 1).any())
        positive += int(has_change)
        negative += int(not has_change)
    if not positive or not negative:
        raise ValueError("Reviewed positive pairs and negative pairs are both required")
    encoder_path = base_manifest.metadata.get("encoder_checkpoint")
    encoder_hash = base_manifest.metadata.get("encoder_sha256")
    if (
        not encoder_path
        or not encoder_hash
        or not base_manifest.metadata.get("encoder_code_commit")
    ):
        raise ValueError("A pretrained encoder checkpoint, SHA256 and code commit must be pinned")
    encoder_path = Path(encoder_path).expanduser()
    if not encoder_path.is_file() or sha256_file(encoder_path) != encoder_hash:
        raise ValueError("Pretrained encoder checksum mismatch")
    import torch

    network = create_satlas_change_model(
        encoder_checkpoint=encoder_path,
        encoder_sha256=encoder_hash,
        decoder_channels=base_manifest.metadata.get("decoder_channels", 64),
    ).to(device)
    trainable = [value for value in network.parameters() if value.requires_grad]
    if not trainable:
        raise ValueError("No trainable change decoder parameters")
    original = [value.detach().cpu().clone() for value in trainable]
    optimizer = torch.optim.AdamW(trainable, lr=learning_rate)
    losses, started = [], time.monotonic()
    for _ in range(epochs):
        network.train()
        for sample in dataset["samples"]:
            if max_seconds is not None and time.monotonic() - started >= max_seconds:
                raise TimeoutError("Change training exceeded the fixed time budget; no model exported")
            # Keep only one bounded pair in memory and recheck its immutable bytes.
            left, right, labels, valid = _reviewed_pair(root, sample, dataset["quality_protocol"])
            pad = ((0, (-labels.shape[0]) % 32), (0, (-labels.shape[1]) % 32))
            inputs = [
                torch.from_numpy(np.pad(value, ((0, 0), *pad))).to(device)[None]
                for value in (left, right)
            ]
            target = torch.from_numpy(np.pad(labels.astype(np.float32), pad)).to(device)[None, None]
            mask = torch.from_numpy(np.pad(valid, pad)).to(device)[None, None]
            optimizer.zero_grad(set_to_none=True)
            loss = masked_change_loss(network(*inputs), target, mask, positive_weight=positive_weight)
            if not torch.isfinite(loss):
                raise ValueError("Non-finite supervised change training loss")
            loss.backward()
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
    changed = any(
        not torch.equal(before, after.detach().cpu()) for before, after in zip(original, trainable)
    )
    if not changed:
        raise ValueError("Training did not update the change decoder")
    result = export_training_artifact(
        network,
        base_manifest,
        destination,
        dataset_version=dataset["version"],
        training={
            "dataset_manifest_sha256": dataset["manifest_sha256"],
            "reserved_manifest_hashes": dataset["reserved_manifest_hashes"],
            "split_groups": sorted(
                {group for sample in dataset["samples"] for group in sample["split_groups"]}
            ),
            "acquisition_ids": sorted(
                {
                    sample[role]["acquisition_key"]
                    for sample in dataset["samples"]
                    for role in ("reference", "target")
                }
            ),
            "observation_ids": sorted(
                {
                    sample[role]["observation_id"]
                    for sample in dataset["samples"]
                    for role in ("reference", "target")
                }
            ),
            "positive_pairs": positive,
            "negative_pairs": negative,
            "epochs": epochs,
            "steps": len(losses),
            "losses": losses,
            "device": device,
            "seconds": time.monotonic() - started,
            "updated_parameters": changed,
            "encoder_frozen": True,
            "positive_weight": positive_weight,
            "encoder_weights_sha256": encoder_hash,
            "quality_protocol": dataset["quality_protocol"],
            "scope": "supervised_training; independent evaluation still required",
        },
    )
    # These parameters define inference, not an approval or validation result.
    for field in ("change_threshold", "min_area_m2", "max_alignment_rmse_pixels",
                  "minimum_observable_fraction"):
        if field in base_manifest.publication_policy:
            result.publication_policy[field] = base_manifest.publication_policy[field]
    result.metadata["training_state"] = "own_supervised_decoder_trained; not_locally_validated"
    pending = Path(destination) / ".manifest.pending.json"
    pending.write_text(json_text(result.model_dump(mode="json")))
    os.replace(pending, Path(destination) / "manifest.json")
    return result


def _observation(connection, bundle: RasterBundle, *, require_visible: bool = True) -> dict:
    if not bundle.observation_id or not bundle.metadata.get("captured_at"):
        raise ValueError("Numeric evidence needs a registered observation ID and capture timestamp")
    row = connection.execute(
        "SELECT o.*, a.captured_at AS acquisition_capture FROM observations o "
        "JOIN acquisitions a USING(acquisition_key) WHERE observation_id=?",
        (bundle.observation_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Change evidence observation is not registered")
    if (
        row["sensor"] != "sentinel-2"
        or bundle.sensor != row["sensor"]
        or utc_text(bundle.metadata["captured_at"]) != utc_text(row["captured_at"])
        or utc_text(row["captured_at"]) != utc_text(row["acquisition_capture"])
    ):
        raise ValueError("Numeric evidence sensor or capture timestamp disagrees with the catalog")
    if row["review_status"] == "rejected":
        raise ValueError("A rejected observation cannot support change evidence")
    if require_visible and (
        row["status"] not in {"usable", "partial"} or not row["observable_fraction"]
    ):
        raise ValueError("Comparison requires observable registered observations")
    return dict(row)


def _same_zone(reference: dict, target: dict) -> None:
    if (reference["zone_id"], reference["port_id"]) != (target["zone_id"], target["port_id"]):
        raise ValueError("Change evidence must belong to the same port and zone")
    if reference["acquisition_key"] == target["acquisition_key"]:
        raise ValueError("Change evidence must use distinct acquisitions")


def _fingerprints(bundle: RasterBundle) -> dict[Path, str]:
    return {
        Path(path): sha256_file(Path(path))
        for path in {*bundle.band_paths.values(), *bundle.mask_paths.values()}
    }


def _persist_bundle(store: Store, bundle: RasterBundle, expected: dict[Path, str]) -> list[str]:
    """Copy complete immutable raster bytes; registration never exposes a partial file."""
    identifiers = []
    for source, digest in sorted(expected.items(), key=lambda item: str(item[0])):
        if sha256_file(source) != digest:
            raise ValueError("Change evidence changed during inference")
        with rasterio.open(source) as raster:
            if raster.crs is None or raster.driver not in {"GTiff", "JP2OpenJPEG"}:
                raise ValueError("Change evidence must be georeferenced numeric rasters")
            bounds = list(raster.bounds)
        bands = [name for name, path in bundle.band_paths.items() if Path(path) == source]
        masks = [name for name, path in bundle.mask_paths.items() if Path(path) == source]
        roles = hashlib.sha256(json_text([sorted(bands), sorted(masks)]).encode()).hexdigest()[:12]
        relative = (
            f"change-evidence/{bundle.observation_id}/{digest}-{roles}{source.suffix.lower()}"
        )
        destination = confined_path(store.data_dir, relative, must_exist=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=destination.parent)
            try:
                with os.fdopen(descriptor, "wb") as output, source.open("rb") as original:
                    shutil.copyfileobj(original, output)
                    output.flush()
                    os.fsync(output.fileno())
                if sha256_file(Path(temporary)) != digest:
                    raise ValueError("Change evidence changed during publication")
                try:
                    os.link(temporary, destination)
                except FileExistsError:
                    pass
            finally:
                Path(temporary).unlink(missing_ok=True)
        if sha256_file(destination) != digest:
            raise ValueError("Stored change evidence checksum mismatch")
        metadata = {
            "mime_type": "image/jp2" if source.suffix.lower() == ".jp2" else "image/tiff",
            "crs": bundle.crs,
            "bounds": bounds,
            "captured_at": bundle.metadata["captured_at"],
            "role": "change_numeric_evidence",
            "attribution": "Contains modified Copernicus Sentinel data",
            "bundle": {
                "bands": bands,
                "masks": masks,
                "band_indices": bundle.metadata.get("band_indices", {}),
                "units": bundle.units,
                "transform": bundle.transform,
                "sensor": bundle.sensor,
                "product_level": bundle.product_level,
                "preprocessing_version": bundle.preprocessing_version,
                "metadata": bundle.metadata,
            },
        }
        with store.transaction() as connection:
            existing = connection.execute(
                "SELECT asset_id,sha256,observation_id FROM assets WHERE path=?", (relative,)
            ).fetchone()
            if existing:
                if (
                    existing["sha256"] != digest
                    or existing["observation_id"] != bundle.observation_id
                ):
                    raise ValueError("Registered change evidence is inconsistent")
                identifier = existing["asset_id"]
            else:
                identifier = store.register_asset(
                    relative,
                    observation_id=bundle.observation_id,
                    metadata=metadata,
                    connection=connection,
                )
        identifiers.append(identifier)
    return identifiers


def _event(row) -> ChangeEvent:
    details = json.loads(row["data_json"])
    return ChangeEvent.model_validate(
        {
            **{key: details[key] for key in ChangeEvent.model_fields if key in details},
            "status": row["status"],
            "geometry": json.loads(row["geometry_json"]),
        }
    )


def _evidence(row: dict, changed: bool | None, *, observable: bool, aligned: bool) -> dict:
    return {
        "observation_id": row["observation_id"],
        "acquisition_key": row["acquisition_key"],
        "captured_at": utc_text(row["captured_at"]),
        "sensor": row["sensor"],
        "changed": changed,
        "observable": observable,
        "aligned": aligned,
    }


def compare_and_register(
    store: Store, reference_bundle: RasterBundle, target_bundle: RasterBundle, model: ModelManifest
) -> list[ChangeEvent]:
    """Compare registered observations, package both inputs, and insert candidates once.

    Repeating a comparison returns the persisted state, including human rejection,
    without resetting reviews, persistence, reversion or their audit history.
    """
    with closing(store.connect()) as connection:
        reference = _observation(connection, reference_bundle)
        target = _observation(connection, target_bundle)
    _same_zone(reference, target)
    fingerprints = [_fingerprints(bundle) for bundle in (reference_bundle, target_bundle)]
    registered = register_portable_model(store, model)
    runtime_model = (
        registered.model_copy(
            update={"weights_path": str(confined_path(store.data_dir, registered.weights_path))}
        )
        if registered.weights_path
        else registered
    )
    events = compare(reference_bundle, target_bundle, runtime_model)
    assets = [
        identifier
        for bundle, expected in zip((reference_bundle, target_bundle), fingerprints)
        for identifier in _persist_bundle(store, bundle, expected)
    ]
    result = []
    with store.transaction() as connection:
        # A review may have changed observability while the model was running.
        reference = _observation(connection, reference_bundle)
        target = _observation(connection, target_bundle)
        for event in events:
            if (
                event.reference_observation_id != reference["observation_id"]
                or event.first_observation_id != target["observation_id"]
                or event.model_id != model.model_id
                or event.status != "candidate"
            ):
                raise ValueError("Comparison returned an inconsistent candidate identity")
            existing = connection.execute(
                "SELECT * FROM changes WHERE change_id=?", (event.event_id,)
            ).fetchone()
            if existing:
                result.append(_event(existing))
                continue
            event.evidence_asset_ids = sorted(set([*event.evidence_asset_ids, *assets]))
            details = {
                **event.model_dump(mode="json"),
                "comparison_grid": {
                    "crs": reference_bundle.crs,
                    "transform": reference_bundle.transform,
                },
                "temporal_evidence": [
                    _evidence(reference, False, observable=True, aligned=True),
                    _evidence(target, True, observable=True, aligned=True),
                ],
            }
            connection.execute(
                "INSERT INTO changes(change_id,observation_id,geometry_json,status,created_at,data_json) VALUES (?,?,?,'candidate',?,?)",
                (
                    event.event_id,
                    target["observation_id"],
                    json_text(event.geometry),
                    utc_text(),
                    json_text(details),
                ),
            )
            result.append(event)
    return result


def _event_visible(bundle: RasterBundle, geometry: dict) -> bool:
    from pyproj import Transformer
    from rasterio.features import geometry_mask
    from shapely.geometry import Polygon, shape
    from shapely.ops import transform

    with rasterio.open(next(iter(bundle.band_paths.values()))) as raster:
        height, width = raster.shape
    grid = Affine(*bundle.transform)
    projected = transform(
        Transformer.from_crs("EPSG:4326", bundle.crs, always_xy=True).transform, shape(geometry)
    )
    extent = Polygon([grid * point for point in ((0, 0), (width, 0), (width, height), (0, height))])
    if not extent.covers(projected):
        return False
    selected = geometry_mask(
        [projected], out_shape=(height, width), transform=grid, invert=True, all_touched=True
    )
    visible = _mask(bundle, "observable", selected.shape) & _mask(
        bundle, "static_area", selected.shape
    )
    for name in ("vessels", "temporary"):
        if name in bundle.mask_paths:
            visible &= ~_mask(bundle, name, selected.shape)
    return bool(selected.any() and visible[selected].all())


def record_temporal_evidence(
    store: Store, event_id: str, bundle: RasterBundle, *, changed: bool | None, author: str
) -> ChangeEvent:
    """Audit an explicit later assessment; only visible aligned optical evidence advances state.

    The assessment comes from a reviewer or a separate comparison; this function
    does not infer change from a detection score. Database dates/acquisition IDs,
    measured comparability and local visibility over the event polygon are checked.
    Unknown or obscured evidence is retained without confirming or reversing.
    """
    if not author.strip() or (changed is not None and type(changed) is not bool):
        raise ValueError("An author and a true/false/unknown temporal assessment are required")
    with closing(store.connect()) as connection:
        stored = connection.execute(
            "SELECT * FROM changes WHERE change_id=?", (event_id,)
        ).fetchone()
        if stored is None:
            raise KeyError(event_id)
        if stored["review_status"] == "rejected" or stored["status"] == "rejected":
            raise ValueError("A human-rejected event cannot be advanced automatically")
        _observation(connection, bundle, require_visible=False)
    assets = _persist_bundle(store, bundle, _fingerprints(bundle))
    with store.transaction() as connection:
        row = connection.execute("SELECT * FROM changes WHERE change_id=?", (event_id,)).fetchone()
        if row["review_status"] == "rejected" or row["status"] == "rejected":
            raise ValueError("A human-rejected event cannot be advanced automatically")
        current, details = _event(row), json.loads(row["data_json"])
        observation = _observation(connection, bundle, require_visible=False)
        original = connection.execute(
            "SELECT * FROM observations WHERE observation_id=?", (current.reference_observation_id,)
        ).fetchone()
        _same_zone(dict(original), observation)
        records = details.get("temporal_evidence")
        if not records:
            raise ValueError(
                "Event lacks auditable temporal evidence; import its original comparison first"
            )
        for evidence in records:
            historical = connection.execute(
                "SELECT * FROM observations WHERE observation_id=?", (evidence["observation_id"],)
            ).fetchone()
            if (
                historical is None
                or historical["acquisition_key"] != evidence["acquisition_key"]
                or utc_text(historical["captured_at"]) != evidence["captured_at"]
            ):
                raise ValueError(
                    "Historical temporal evidence no longer matches its registered acquisition"
                )
            if historical["review_status"] == "rejected" or (
                evidence["observable"]
                and (
                    historical["status"] not in {"usable", "partial"}
                    or not historical["observable_fraction"]
                )
            ):
                raise ValueError("Historical temporal evidence is rejected or no longer observable")
        if (
            observation["acquisition_key"] in {item["acquisition_key"] for item in records}
            or utc_text(observation["captured_at"]) <= records[-1]["captured_at"]
        ):
            raise ValueError("Temporal evidence must use a distinct later acquisition")
        model_row = connection.execute(
            "SELECT data_json FROM models WHERE model_id=?", (current.model_id,)
        ).fetchone()
        if model_row is None:
            raise ValueError("The event's model manifest is missing")
        policy = json.loads(model_row[0])["publication_policy"]
        grid = details["comparison_grid"]
        aligned = bundle.crs == grid["crs"] and np.allclose(
            bundle.transform, grid["transform"], rtol=0, atol=1e-9
        )
        try:
            _quality(policy, bundle)
        except ValueError:
            aligned = False
        observable = (
            observation["status"] in {"usable", "partial"}
            and bool(observation["observable_fraction"])
            and _event_visible(bundle, current.geometry)
        )
        assessment = _evidence(observation, changed, observable=observable, aligned=bool(aligned))
        records.append(assessment)
        temporal = [
            TemporalEvidence(
                **{
                    **item,
                    "captured_at": datetime.fromisoformat(
                        item["captured_at"].replace("Z", "+00:00")
                    ),
                }
            )
            for item in records
        ]
        evolved = track_change(
            temporal[0],
            temporal[1:],
            geometry=current.geometry,
            model_id=current.model_id,
            event_id=event_id,
        )
        if evolved is None:
            raise ValueError("Event has no valid initial changed observation")
        entry = {**evolved.history[-1], "author": author.strip(), "evidence_asset_ids": assets}
        updated = current.model_copy(
            update={
                "status": evolved.status,
                "confirming_observation_id": evolved.confirming_observation_id,
                "history": [*current.history, entry],
                "evidence_asset_ids": sorted(set([*current.evidence_asset_ids, *assets])),
            }
        )
        previous = connection.execute(
            "SELECT revision_id FROM revisions WHERE entity_type='change' AND entity_id=? ORDER BY rowid DESC LIMIT 1",
            (event_id,),
        ).fetchone()
        revision_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO revisions(revision_id,entity_type,entity_id,previous_revision_id,proposal_json,correction_json,author,created_at) VALUES (?,'change',?,?,?,?,?,?)",
            (
                revision_id,
                event_id,
                previous[0] if previous else None,
                json_text(dict(row)),
                json_text({"temporal_evidence": assessment, "evidence_asset_ids": assets}),
                author.strip(),
                utc_text(),
            ),
        )
        details.update(updated.model_dump(mode="json"))
        details["temporal_evidence"] = records
        details["latest_revision_id"] = revision_id
        connection.execute(
            "UPDATE changes SET status=?,data_json=? WHERE change_id=?",
            (updated.status, json_text(details), event_id),
        )
    return updated
