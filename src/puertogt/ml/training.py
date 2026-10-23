"""Explicit supervised training from reviewed labels and pinned frozen features."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

from puertogt.contracts import ModelManifest
from puertogt.store import confined_path, json_text, sha256_file, utc_text


def validate_training_dataset(dataset_dir: Path | str, *, reserved_manifests: Iterable[Path] = ()) -> dict[str, Any]:
    root = Path(dataset_dir).resolve()
    manifest_path = confined_path(root, "manifest.json")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("split") != "train":
        raise ValueError("Training requires the train split; calibration and test are forbidden")
    if not manifest.get("samples") or not manifest.get("version"):
        raise ValueError("A versioned, nonempty reviewed dataset is required")
    groups = set()
    observations = set()
    for sample in manifest["samples"]:
        if sample.get("split", "train") != "train":
            raise ValueError("Every training sample must belong to train")
        observation = sample["observation_id"]
        if observation in observations:
            raise ValueError("Duplicate observation in training dataset")
        observations.add(observation)
        groups.update(sample.get("split_groups", []))
        labels = confined_path(root, sample["labels"])
        if sha256_file(labels) != sample["sha256"]:
            raise ValueError("Reviewed label checksum mismatch")
    reserved_hashes = []
    for path in reserved_manifests:
        reserved = json.loads(Path(path).read_text())
        if reserved.get("split") not in {"calibration", "test"}:
            raise ValueError("Reserved manifests must belong to calibration or test")
        for sample in reserved["samples"]:
            if sample["observation_id"] in observations or groups.intersection(sample.get("split_groups", [])):
                raise ValueError("Training and reserved datasets contain related samples")
        reserved_hashes.append(sha256_file(Path(path)))
    return {**manifest, "manifest_sha256": sha256_file(manifest_path), "reserved_manifest_hashes": reserved_hashes}


def freeze_encoder(model: Any) -> int:
    encoder = getattr(model, "encoder", None)
    if encoder is None:
        raise ValueError("The model must expose its encoder explicitly")
    count = 0
    for parameter in encoder.parameters():
        parameter.requires_grad_(False)
        count += parameter.numel()
    encoder.eval()
    return count


def export_training_artifact(model: Any, base_manifest: ModelManifest, destination: Path | str,
                             *, training: dict[str, Any], dataset_version: str) -> ModelManifest:
    """Export a new immutable identity, keeping the parent and its policy intact."""
    import torch

    destination = Path(destination).absolute()
    if destination.exists():
        raise FileExistsError("Trained model versions are immutable")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.pending-", dir=destination.parent))
    try:
        checkpoint = stage / "weights.pt"
        torch.save({"state_dict": {key: tensor.detach().cpu() for key, tensor in model.state_dict().items()}}, checkpoint)
        # Saved paths are relative so a model directory can be moved independently.
        # A run needs its own identity even when equal weights are reproduced:
        # timestamps, dataset provenance and policy still belong to this artifact.
        artifact_id = uuid.uuid4().hex
        result = base_manifest.model_copy(update={
            "model_id": f"{base_manifest.model_id}-trained-{artifact_id}",
            "version": f"{base_manifest.version}+trained.{artifact_id}",
            "weights_path": "weights.pt", "weights_sha256": sha256_file(checkpoint), "dataset_version": dataset_version,
            "metrics": {}, "publication_policy": {"status": "experimental", "automatic_publication": False,
                "local_validation": "not_evaluated", "fields": {}},
            "metadata": {**base_manifest.metadata, "training": training, "created_at": utc_text(),
                         "parent_model": {"model_id": base_manifest.model_id, "version": base_manifest.version,
                                          "weights_sha256": base_manifest.weights_sha256},
                         "training_code_sha256": sha256_file(Path(__file__)),
                         "recipe_sha256": hashlib.sha256(base_manifest.model_dump_json().encode()).hexdigest(),
                         "runtime": {"torch": str(torch.__version__), "numpy": str(np.__version__)}},
        })
        (stage / "manifest.json").write_text(result.model_dump_json(indent=2) + "\n")
        stage.rename(destination)
        return result
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def train_reviewed_dataset(dataset_dir: Path | str, model: Any, input_loader: Callable,
                           loss_function: Callable, base_manifest: ModelManifest, destination: Path | str,
                           *, epochs: int = 1, learning_rate: float = 1e-4, device: str = "cpu",
                           reserved_manifests: Iterable[Path] = ()) -> ModelManifest:
    """Generic real training loop for a task-specific model and reviewed-scene loader.

    ``input_loader(root, sample)`` returns inputs and targets; ``loss_function``
    receives model output and targets. Frozen encoders remain in evaluation mode.
    """
    import torch

    dataset = validate_training_dataset(dataset_dir, reserved_manifests=reserved_manifests)
    if epochs < 1 or learning_rate <= 0 or not math.isfinite(learning_rate):
        raise ValueError("Training epochs and learning rate must be positive")
    model.to(device)
    trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
    if not trainable:
        raise ValueError("No trainable parameters")
    original = [parameter.detach().cpu().clone() for parameter in trainable]
    optimizer = torch.optim.AdamW(trainable, lr=learning_rate)
    losses = []
    started = time.monotonic()
    for _ in range(epochs):
        model.train()
        encoder = getattr(model, "encoder", None)
        if encoder is not None and all(not parameter.requires_grad for parameter in encoder.parameters()):
            encoder.eval()
        for sample in dataset["samples"]:
            inputs, targets = input_loader(Path(dataset_dir), sample)
            optimizer.zero_grad(set_to_none=True)
            loss = loss_function(model(inputs), targets)
            if not torch.isfinite(loss):
                raise ValueError("Non-finite supervised training loss")
            loss.backward()
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
    changed = any(not torch.equal(before, after.detach().cpu()) for before, after in zip(original, trainable))
    if not changed:
        raise ValueError("Training did not change any parameter")
    return export_training_artifact(model, base_manifest, destination, dataset_version=dataset["version"], training={
        "dataset_manifest_sha256": dataset["manifest_sha256"], "reserved_manifest_hashes": dataset["reserved_manifest_hashes"],
        "epochs": epochs, "steps": len(losses), "losses": losses, "device": device,
        "seconds": time.monotonic() - started, "updated_parameters": changed,
        "scope": "supervised_training; independent evaluation still required",
    })


def _has_reference(properties: dict, field: str) -> bool:
    reference = properties.get("provenance", {}).get(field, {})
    return reference.get("kind") in {"public_dataset", "independent_reference"} and bool(reference.get("reference"))


def train_attribute_dataset(dataset_dir: Path | str, base_manifest: ModelManifest, destination: Path | str,
                            *, epochs: int = 5, learning_rate: float = 1e-3, batch_size: int = 32,
                            seed: int = 0, device: str = "cpu",
                            reserved_manifests: Iterable[Path] = ()) -> ModelManifest:
    """Train real sensor-specific heads on cached, hash-pinned encoder features."""
    from puertogt.ml.attributes import FAMILIES, build_attribute_head, masked_attribute_loss

    dataset = validate_training_dataset(dataset_dir, reserved_manifests=reserved_manifests)
    if base_manifest.task != "attributes":
        raise ValueError("An attribute model recipe is required")
    if min(epochs, batch_size) < 1 or not math.isfinite(learning_rate) or learning_rate <= 0:
        raise ValueError("Invalid training settings")
    encoder_hash = base_manifest.metadata.get("encoder_manifest", {}).get("weights_sha256")
    if not encoder_hash:
        raise ValueError("A pinned encoder fingerprint is required for cached features")
    taxonomy = base_manifest.metadata.get("taxonomy", list(FAMILIES))
    feature_dim = base_manifest.metadata["feature_dim"]
    dimension_scale = np.asarray(base_manifest.metadata.get("dimension_scale", [100, 20]), dtype=np.float32)
    if dimension_scale.shape != (2,) or not np.isfinite(dimension_scale).all() or (dimension_scale <= 0).any():
        raise ValueError("Dimension scales must be two positive finite values")
    features, families, dimensions, family_valid, dimension_valid = [], [], [], [], []
    for sample in dataset["samples"]:
        cache = sample.get("feature_cache")
        if not cache:
            raise ValueError("Missing feature_cache; extract the pinned encoder features first")
        if (cache.get("encoder_sha256") != encoder_hash or cache.get("pooling") != "roi_mean"
                or cache.get("feature_level") != base_manifest.metadata.get("feature_level", 0)):
            raise ValueError("Feature cache does not match the frozen encoder recipe")
        if cache.get("context_policy") != base_manifest.metadata.get("feature_context"):
            raise ValueError("Feature cache context policy does not match the frozen encoder recipe")
        path = confined_path(Path(dataset_dir), cache["path"])
        if sha256_file(path) != cache["sha256"]:
            raise ValueError("Feature cache checksum mismatch")
        with np.load(path, allow_pickle=False) as values:
            encoded, identities = values["features"], values["ids"].astype(str).tolist()
        if encoded.shape != (len(identities), feature_dim) or not np.isfinite(encoded).all() or len(set(identities)) != len(identities):
            raise ValueError("Invalid cached feature shape, IDs or values")
        index = dict(zip(identities, encoded))
        collection = json.loads(confined_path(Path(dataset_dir), sample["labels"]).read_text())
        for feature in collection["features"]:
            properties = feature["properties"]
            identity = properties["id"]
            if identity not in index:
                raise ValueError("Reviewed object is missing its encoder features")
            family = properties.get("family")
            valid_family = family in taxonomy and _has_reference(properties, "family")
            dim_labels = [properties.get(field) for field in ("length_m", "width_m")]
            valid_dim = [value is not None and _has_reference(properties, field)
                         for field, value in zip(("length_m", "width_m"), dim_labels)]
            if any(valid and (not math.isfinite(value) or value <= 0) for valid, value in zip(valid_dim, dim_labels)):
                raise ValueError("Reviewed dimensions must be positive finite metres")
            features.append(index[identity])
            families.append(taxonomy.index(family) if valid_family else -1)
            dimensions.append([value / scale if valid else float("nan") for value, valid, scale in zip(dim_labels, valid_dim, dimension_scale)])
            family_valid.append(valid_family)
            dimension_valid.append(valid_dim)
    if not features or not (any(family_valid) or any(any(row) for row in dimension_valid)):
        raise ValueError("No independently supported attribute labels available for training")
    import torch
    torch.manual_seed(seed)
    network = build_attribute_head(feature_dim, hidden_dim=base_manifest.metadata.get("hidden_dim", 128), num_families=len(taxonomy)).to(device)
    before = {name: value.detach().cpu().clone() for name, value in network.state_dict().items()}
    optimizer = torch.optim.AdamW(network.parameters(), lr=learning_rate)
    arrays = [torch.tensor(np.asarray(features), dtype=torch.float32, device=device),
              torch.tensor(families, dtype=torch.int64, device=device),
              torch.tensor(dimensions, dtype=torch.float32, device=device),
              torch.tensor(family_valid, dtype=torch.bool, device=device),
              torch.tensor(dimension_valid, dtype=torch.bool, device=device)]
    losses = []
    started = time.monotonic()
    for _ in range(epochs):
        network.train()
        order = torch.randperm(len(features), device=device)
        for offset in range(0, len(features), batch_size):
            indexes = order[offset:offset + batch_size]
            optimizer.zero_grad(set_to_none=True)
            output = network(arrays[0][indexes])
            loss = masked_attribute_loss(output["family_logits"], output["dimensions"], *[value[indexes] for value in arrays[1:]])
            if not torch.isfinite(loss):
                raise ValueError("Non-finite attribute training loss")
            loss.backward()
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
    changed = any(not torch.equal(before[name], value.detach().cpu()) for name, value in network.state_dict().items())
    if not changed:
        raise ValueError("Training did not update the attribute head")
    configured = base_manifest.model_copy(update={"metadata": {**base_manifest.metadata,
        "architecture": "pooled_fpn_mlp_v1", "taxonomy": taxonomy, "dimension_scale": dimension_scale.tolist()}})
    dimension_baseline = {
        field: float(np.median([row[index] * dimension_scale[index] for row, valid in zip(dimensions, dimension_valid)
                                if valid[index]])) if any(row[index] for row in dimension_valid) else None
        for index, field in enumerate(("length_m", "width_m"))
    }
    return export_training_artifact(network, configured, destination, dataset_version=dataset["version"], training={
        "dataset_manifest_sha256": dataset["manifest_sha256"], "reserved_manifest_hashes": dataset["reserved_manifest_hashes"],
        "split_groups": sorted({group for sample in dataset["samples"] for group in sample.get("split_groups", [])}),
        "observation_ids": [sample["observation_id"] for sample in dataset["samples"]],
        "dimension_baseline_m": dimension_baseline,
        "dimension_baseline_method": "median of independently supported train labels only",
        "encoder_weights_sha256": encoder_hash, "encoder_frozen": True, "feature_source": "cached_public_encoder_roi_mean",
        "epochs": epochs, "steps": len(losses), "losses": losses, "seed": seed, "device": device,
        "seconds": time.monotonic() - started, "updated_parameters": changed,
        "label_support": {"family": sum(family_valid), "length_m": sum(row[0] for row in dimension_valid),
                          "width_m": sum(row[1] for row in dimension_valid)},
        "scope": "supervised_training; independent evaluation still required",
    })


def cache_reviewed_features(dataset_dir: Path | str, encoder_manifest: ModelManifest,
                            bundle_loader: Callable, *, feature_level: int = 0) -> Path:
    """Attach reusable FPN features to a working copy of a reviewed training dataset.

    ``bundle_loader(sample)`` resolves the sample's registered numeric assets.
    Existing label files remain untouched, and each cache is hashed in the manifest.
    """
    from puertogt.contracts import Prediction
    from puertogt.ml.attributes import extract_features
    from puertogt.ml.detection import load_detector

    root = Path(dataset_dir).resolve()
    manifest = validate_training_dataset(root)
    detector = load_detector(encoder_manifest)
    for sample in manifest["samples"]:
        if sample.get("feature_cache"):
            raise FileExistsError("Feature caches are immutable; use a new dataset version")
        labels = json.loads(confined_path(root, sample["labels"]).read_text())
        detections = [Prediction(prediction_id=value["properties"]["id"], geometry=value["geometry"], detection_score=1)
                      for value in labels["features"]]
        features = extract_features(bundle_loader(sample), detections, encoder_manifest,
                                    feature_level=feature_level, detector=detector)
        name = f"{sample['observation_id']}.features.npz"
        target = confined_path(root, name, must_exist=False)
        np.savez_compressed(target, features=features, ids=np.asarray([value.prediction_id for value in detections], dtype=str))
        sample["feature_cache"] = {"path": name, "sha256": sha256_file(target), "encoder_sha256": encoder_manifest.weights_sha256,
                                   "pooling": "roi_mean", "feature_level": feature_level}
    manifest.pop("manifest_sha256", None)
    manifest.pop("reserved_manifest_hashes", None)
    target = root / "manifest.json"
    staged = root / ".manifest.pending.json"
    staged.write_text(json_text(manifest) + "\n")
    staged.replace(target)
    return target
