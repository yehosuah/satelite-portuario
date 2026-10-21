"""Sensor-specific learned heads on frozen detector features, with field abstention."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from puertogt.contracts import ModelManifest, Prediction, RasterBundle
from puertogt.store import sha256_file

FAMILIES = ("carga", "tanquero", "pasajeros", "remolcador", "pesquero", "otros")


def load_attribute_manifest(path: Path | str) -> ModelManifest:
    import yaml

    from puertogt.ml.detection import load_manifest

    path = Path(path)
    value = yaml.safe_load(path.read_text())
    metadata = value.setdefault("metadata", {})
    if metadata.get("encoder_manifest_path") and not metadata.get("encoder_manifest"):
        encoder_path = (path.parent / metadata["encoder_manifest_path"]).resolve()
        metadata["encoder_manifest"] = load_manifest(encoder_path).model_dump(mode="json")
    if value.get("weights_path") and not Path(value["weights_path"]).is_absolute():
        candidate = path.parent / value["weights_path"]
        if candidate.is_file():
            value["weights_path"] = str(candidate.resolve())
    return ModelManifest.model_validate(value)


def build_attribute_head(feature_dim: int, *, hidden_dim: int = 128, num_families: int = len(FAMILIES)):
    import torch
    from torch import nn

    if min(feature_dim, hidden_dim, num_families) < 1:
        raise ValueError("Head dimensions must be positive")

    class AttributeHead(nn.Module):
        def __init__(self):
            super().__init__()
            self.shared = nn.Sequential(nn.Linear(feature_dim, hidden_dim), nn.ReLU())
            self.family = nn.Linear(hidden_dim, num_families)
            self.dimensions = nn.Linear(hidden_dim, 2)

        def forward(self, features):
            encoded = self.shared(features)
            return {"family_logits": self.family(encoded),
                    "dimensions": torch.nn.functional.softplus(self.dimensions(encoded))}

    return AttributeHead()


def masked_attribute_loss(family_logits, dimensions, family_target, dimension_target,
                          family_valid, dimension_valid, *, regression_weight: float = 1.0):
    """Only independently supported labels contribute to each attribute's loss."""
    import torch
    import torch.nn.functional as functional

    if family_logits.ndim != 2 or dimensions.shape != dimension_target.shape or dimensions.shape[-1] != 2:
        raise ValueError("Incompatible attribute tensor shapes")
    if family_valid.shape != family_target.shape or dimension_valid.shape != dimension_target.shape:
        raise ValueError("Attribute label masks must match their targets")
    if not torch.isfinite(family_logits).all() or not torch.isfinite(dimensions).all():
        raise ValueError("Non-finite attribute predictions")
    family_mask = family_valid.bool()
    dimension_mask = dimension_valid.bool() & torch.isfinite(dimension_target)
    loss = family_logits.sum() * 0 + dimensions.sum() * 0
    if family_mask.any():
        target = family_target[family_mask]
        if (target < 0).any() or (target >= family_logits.shape[1]).any():
            raise ValueError("Valid family labels must lie in the declared taxonomy")
        loss = loss + functional.cross_entropy(family_logits[family_mask], target.long())
    if dimension_mask.any():
        if (dimension_target[dimension_mask] <= 0).any():
            raise ValueError("Valid dimensions must be positive")
        loss = loss + regression_weight * functional.smooth_l1_loss(dimensions[dimension_mask], dimension_target[dimension_mask])
    return loss


def apply_attribute_policy(detection: Prediction, values: Mapping[str, Any], policies: Mapping[str, Any]) -> Prediction:
    updates: dict[str, Any] = {"family": None, "length_m": None, "width_m": None}
    quality = dict(detection.field_quality)
    for field in updates:
        policy = policies.get(field, {})
        reason = None
        if not policy.get("validated"):
            reason = "field_not_validated"
        elif not policy.get("min_support") or policy.get("reference_support", 0) < policy["min_support"]:
            reason = "insufficient_reference_support"
        elif field not in values or values[field] is None:
            reason = "no_estimate"
        elif field == "family":
            probability = values.get("family_probability")
            threshold = policy.get("min_probability")
            if values[field] not in policy.get("allowed_families", []) or values[field] not in FAMILIES:
                reason = "family_outside_validated_scope"
            elif (threshold is None or probability is None or not math.isfinite(probability)
                  or not 0 <= threshold <= 1 or probability < threshold):
                reason = "below_calibrated_threshold"
        else:
            if not math.isfinite(values[field]) or values[field] <= 0:
                reason = "invalid_dimension"
            elif (policy.get("mae_m") is None or policy.get("range_m") is None
                  or not policy["range_m"][0] <= values[field] <= policy["range_m"][1]):
                reason = "dimension_outside_validated_scope"
        if reason:
            quality[field] = {"status": "indeterminate", "reason": reason}
        else:
            updates[field] = values[field]
            quality[field] = {"status": "validated", "reference_support": policy["reference_support"]}
            if field != "family":
                quality[field]["observed_mae_m"] = policy["mae_m"]
    updates["field_quality"] = quality
    return detection.model_copy(update=updates)


def extract_features(bundle: RasterBundle, detections: list[Prediction], encoder_manifest: ModelManifest,
                     *, feature_level: int = 0, detector=None) -> np.ndarray:
    """ROI mean pooling of learned FPN channels; never infer metres from box extents."""
    from pyproj import Transformer
    from rasterio import Affine
    from shapely.geometry import shape
    from shapely.ops import transform as project_geometry

    from puertogt.ml.detection import load_detector
    from puertogt.preprocess import read_model_input

    image = read_model_input(bundle, encoder_manifest)
    if max(image.shape[-2:]) > 512:
        raise ValueError("Attribute feature extraction currently requires a crop no larger than 512 pixels")
    detector = detector or load_detector(encoder_manifest)
    pad_h, pad_w = (-image.shape[1]) % 32, (-image.shape[2]) % 32
    padded = np.pad(image, ((0, 0), (0, pad_h), (0, pad_w)))
    levels = detector.features(padded)
    if not 0 <= feature_level < len(levels):
        raise ValueError("Feature level is outside the pinned encoder")
    feature_map = levels[feature_level][0].detach().cpu().numpy()
    inverse = ~Affine(*bundle.transform)
    project = Transformer.from_crs("EPSG:4326", bundle.crs, always_xy=True)
    features = []
    for prediction in detections:
        geometry = project_geometry(project.transform, shape(prediction.geometry))
        xmin, ymin, xmax, ymax = geometry.bounds
        corners = [inverse * (x, y) for x, y in ((xmin, ymin), (xmin, ymax), (xmax, ymin), (xmax, ymax))]
        x1 = max(0, math.floor(min(p[0] for p in corners) * feature_map.shape[2] / padded.shape[2]))
        y1 = max(0, math.floor(min(p[1] for p in corners) * feature_map.shape[1] / padded.shape[1]))
        x2 = min(feature_map.shape[2], math.ceil(max(p[0] for p in corners) * feature_map.shape[2] / padded.shape[2]))
        y2 = min(feature_map.shape[1], math.ceil(max(p[1] for p in corners) * feature_map.shape[1] / padded.shape[1]))
        if x2 <= x1 or y2 <= y1:
            raise ValueError("Detection lies outside the encoder crop")
        features.append(feature_map[:, y1:y2, x1:x2].mean(axis=(1, 2)))
    return np.asarray(features, dtype=np.float32).reshape(len(detections), feature_map.shape[0])


def estimate_attributes(bundle: RasterBundle, detections: list[Prediction], model: ModelManifest) -> list[dict[str, Any]]:
    """Return raw learned estimates for evaluation/calibration, without publication gates.

    These values are experimental model outputs. Calling this function never
    validates a field and does not modify the model's publication policy.
    """
    if model.task != "attributes" or model.sensor != bundle.sensor:
        raise ValueError("A matching sensor-specific attributes manifest is required")
    if not detections:
        return []
    if not model.weights_path:
        raise ValueError("Trained attribute weights are required for raw estimation")
    path = Path(model.weights_path)
    if not model.weights_sha256 or sha256_file(path) != model.weights_sha256:
        raise ValueError("Attribute checkpoint SHA256 mismatch or missing")
    if model.metadata.get("architecture") != "pooled_fpn_mlp_v1":
        raise ValueError("Unsupported attribute architecture")
    encoder_values = model.metadata.get("encoder_manifest")
    if not isinstance(encoder_values, dict):
        raise ValueError("A pinned encoder_manifest is required for learned attribute features")
    encoder = ModelManifest.model_validate(encoder_values)
    if encoder.sensor != model.sensor or not encoder.weights_sha256:
        raise ValueError("Attribute encoder does not match the sensor or lacks its hash")
    features = extract_features(bundle, detections, encoder, feature_level=model.metadata.get("feature_level", 0))
    import torch
    taxonomy = model.metadata.get("taxonomy", list(FAMILIES))
    if any(label not in FAMILIES for label in taxonomy) or len(set(taxonomy)) != len(taxonomy):
        raise ValueError("Invalid attribute taxonomy")
    network = build_attribute_head(model.metadata["feature_dim"], hidden_dim=model.metadata.get("hidden_dim", 128), num_families=len(taxonomy))
    checkpoint = torch.load(path, weights_only=True, map_location="cpu")
    network.load_state_dict(checkpoint["state_dict"], strict=True)
    network.eval()
    with torch.inference_mode():
        output = network(torch.from_numpy(features))
        probabilities = torch.softmax(output["family_logits"], dim=1).numpy()
        dimensions = output["dimensions"].numpy() * np.asarray(model.metadata.get("dimension_scale", [100, 20]))
    if not np.isfinite(probabilities).all() or not np.isfinite(dimensions).all():
        raise ValueError("Attribute model returned non-finite estimates")
    estimates = []
    for index, detection in enumerate(detections):
        family_index = int(probabilities[index].argmax())
        estimates.append({
            "prediction_id": detection.prediction_id,
            "family": taxonomy[family_index], "family_probability": float(probabilities[index, family_index]),
            "family_probabilities": {label: float(probabilities[index, position]) for position, label in enumerate(taxonomy)},
            "length_m": float(dimensions[index, 0]), "width_m": float(dimensions[index, 1]),
        })
    return estimates


def predict_attributes(bundle: RasterBundle, detections: list[Prediction], model: ModelManifest) -> list[Prediction]:
    """Apply publication policy independently to raw, learned estimates."""
    if model.task != "attributes" or model.sensor != bundle.sensor:
        raise ValueError("A matching sensor-specific attributes manifest is required")
    policies = model.publication_policy.get("fields", {})
    if not model.weights_path:
        return [apply_attribute_policy(item, {}, policies) for item in detections]
    estimates = estimate_attributes(bundle, detections, model)
    return [apply_attribute_policy(detection, values, policies) for detection, values in zip(detections, estimates)]
