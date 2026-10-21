"""Learned optical change segmentation and explicit t0/t1/t2 evidence history."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np

from puertogt.contracts import ChangeEvent, ModelManifest, RasterBundle, as_utc
from puertogt.store import json_text, sha256_file, utc_text


@dataclass(frozen=True)
class TemporalEvidence:
    observation_id: str
    captured_at: datetime
    changed: bool | None
    observable: bool = True
    aligned: bool = True
    sensor: str = "sentinel-2"
    acquisition_key: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "captured_at", as_utc(self.captured_at))
        if not self.observation_id:
            raise ValueError("Evidence requires an observation ID")
        if self.changed is not None and type(self.changed) is not bool:
            raise ValueError("Change evidence must be true, false or unknown")


def track_change(reference: TemporalEvidence, observations: Iterable[TemporalEvidence], *,
                  geometry: dict, model_id: str, event_id: str | None = None) -> ChangeEvent | None:
    """Unknown visibility never confirms, reverses, or narrows an event interval."""
    if (not reference.observable or not reference.aligned or reference.changed is not False
            or reference.sensor != "sentinel-2"):
        raise ValueError("Reference must be a valid, aligned optical observation without change")
    previous = reference.captured_at
    identities = {reference.acquisition_key or reference.observation_id}
    state = None
    first = None
    confirming = None
    history = []
    for evidence in observations:
        identity = evidence.acquisition_key or evidence.observation_id
        if evidence.captured_at <= previous or identity in identities:
            raise ValueError("Temporal evidence must contain distinct acquisitions in chronological order")
        previous = evidence.captured_at
        identities.add(identity)
        valid = evidence.observable and evidence.aligned and evidence.changed is not None and evidence.sensor == "sentinel-2"
        transition = False
        reason = None
        if not valid:
            reason = "not_observable_or_comparable"
        elif state is None:
            if evidence.changed:
                state, first, transition = "candidate", evidence, True
            else:
                reference = evidence  # Tighten t0 to the most recent valid unchanged view.
        elif state == "candidate" and evidence.changed:
            state, confirming, transition = "persistent", evidence, True
        elif state in {"candidate", "persistent"} and not evidence.changed:
            state, transition = "reverted", True
        history.append({"observation_id": evidence.observation_id, "captured_at": utc_text(evidence.captured_at),
                        "changed": evidence.changed if valid else None, "valid": valid,
                        "state": state, "transition": transition, "reason": reason})
    if first is None:
        return None
    identifier = event_id or hashlib.sha256(
        f"{reference.observation_id}:{first.observation_id}:{model_id}:{json_text(geometry)}".encode()
    ).hexdigest()[:24]
    return ChangeEvent(event_id=identifier, geometry=geometry, reference_observation_id=reference.observation_id,
                       first_observation_id=first.observation_id,
                       confirming_observation_id=confirming.observation_id if confirming else None,
                       interval_start=reference.captured_at, interval_end=first.captured_at,
                       status=state, model_id=model_id, history=history)


def build_siamese_change_model(encoder, feature_channels: list[int], *, decoder_channels: int = 64,
                               freeze_encoder: bool = True):
    """Shared encoder, temporal feature fusion and a multiscale U-Net-style decoder."""
    import torch
    import torch.nn.functional as functional
    from torch import nn

    if not feature_channels or min(*feature_channels, decoder_channels) <= 0:
        raise ValueError("Positive encoder and decoder channels are required")

    class SiameseChangeModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder = encoder
            self.encoder_frozen = freeze_encoder
            if freeze_encoder:
                for parameter in self.encoder.parameters():
                    parameter.requires_grad_(False)
            self.fusion = nn.ModuleList([nn.Sequential(nn.Conv2d(3 * channels, decoder_channels, 3, padding=1), nn.ReLU(),
                                                        nn.Conv2d(decoder_channels, decoder_channels, 3, padding=1), nn.ReLU())
                                         for channels in feature_channels])
            self.decoder = nn.ModuleList([nn.Sequential(nn.Conv2d(2 * decoder_channels, decoder_channels, 3, padding=1), nn.ReLU(),
                                                         nn.Conv2d(decoder_channels, decoder_channels, 3, padding=1), nn.ReLU())
                                          for _ in range(len(feature_channels) - 1)])
            self.output = nn.Conv2d(decoder_channels, 1, 1)

        def train(self, mode=True):
            super().train(mode)
            if self.encoder_frozen:
                self.encoder.eval()
            return self

        def forward(self, before, after=None):
            if after is None:
                before, after = before
            if before.shape != after.shape or before.ndim != 4:
                raise ValueError("Siamese inputs must use identical BCHW grids")
            if self.encoder_frozen:
                self.encoder.eval()
                with torch.no_grad():
                    left, right = self.encoder(before), self.encoder(after)
            else:
                left, right = self.encoder(before), self.encoder(after)
            if len(left) != len(feature_channels) or len(right) != len(feature_channels):
                raise ValueError("Encoder feature pyramid does not match its recipe")
            fused = [layer(torch.cat([a, b, torch.abs(a - b)], dim=1))
                     for layer, a, b in zip(self.fusion, left, right)]
            value = fused[-1]
            for layer, skip in zip(self.decoder, reversed(fused[:-1])):
                value = functional.interpolate(value, size=skip.shape[-2:], mode="bilinear", align_corners=False)
                value = layer(torch.cat([value, skip], dim=1))
            return functional.interpolate(self.output(value), size=before.shape[-2:], mode="bilinear", align_corners=False)

    return SiameseChangeModel()


def create_satlas_change_model(*, encoder_checkpoint: Path | None = None,
                               encoder_sha256: str | None = None, decoder_channels: int = 64,
                               _restore_full_checkpoint: bool = False):
    """Instantiate the documented Satlas encoder without any implicit download."""
    if encoder_checkpoint is None and not _restore_full_checkpoint:
        raise ValueError("New change training requires a pinned pretrained encoder checkpoint")
    import torch
    try:
        from satlaspretrain_models.model import Model
        from satlaspretrain_models.utils import Backbone
    except ImportError as error:
        raise RuntimeError("Install the pinned optional ml dependencies for the Satlas encoder") from error

    weights = None
    if encoder_checkpoint is not None:
        if not encoder_sha256 or sha256_file(encoder_checkpoint) != encoder_sha256:
            raise ValueError("Satlas encoder checksum mismatch or missing")
        weights = torch.load(encoder_checkpoint, map_location="cpu", weights_only=True)
    encoder = Model(num_channels=9, multi_image=False, backbone=Backbone.SWINT, fpn=False, weights=weights)
    return build_siamese_change_model(encoder, [96, 192, 384, 768], decoder_channels=decoder_channels)


def masked_change_loss(logits, targets, valid_mask):
    import torch
    import torch.nn.functional as functional

    if logits.shape != targets.shape or logits.shape != valid_mask.shape:
        raise ValueError("Change labels, predictions and observability masks must share a grid")
    valid = valid_mask.bool() & torch.isfinite(targets)
    if not valid.any():
        raise ValueError("No observable change labels in this batch")
    if not torch.isfinite(logits[valid]).all() or ((targets[valid] < 0) | (targets[valid] > 1)).any():
        raise ValueError("Invalid binary change labels or logits")
    return functional.binary_cross_entropy_with_logits(logits[valid], targets[valid])


def load_change_model(model: ModelManifest):
    if model.task != "change" or model.metadata.get("architecture") != "satlas_swint_siamese_unet_v1":
        raise ValueError("A supported optical change recipe is required")
    if not model.weights_path or not model.weights_sha256:
        raise ValueError("Own trained change weights and SHA256 are required; no heuristic fallback")
    path = Path(model.weights_path)
    if sha256_file(path) != model.weights_sha256:
        raise ValueError("Change checkpoint checksum mismatch")
    import torch
    network = create_satlas_change_model(decoder_channels=model.metadata.get("decoder_channels", 64),
                                         _restore_full_checkpoint=True)
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    network.load_state_dict(checkpoint["state_dict"], strict=True)
    return network.eval()


def _mask(bundle: RasterBundle, key: str, shape: tuple[int, int]) -> np.ndarray:
    import rasterio
    from rasterio import Affine
    if key not in bundle.mask_paths:
        raise ValueError(f"Change comparison requires the {key} mask")
    with rasterio.open(bundle.mask_paths[key]) as source:
        if source.shape != shape or str(source.crs) != bundle.crs or not source.transform.almost_equals(Affine(*bundle.transform)):
            raise ValueError("Comparison masks are not aligned with numeric inputs")
        return source.read(1) > 0


def compare(reference: RasterBundle, target: RasterBundle, model: ModelManifest) -> list[ChangeEvent]:
    """Return learned candidate events; persistence always needs separate t2 evidence."""
    if not model.weights_path or not model.weights_sha256:
        raise ValueError("Own trained change weights are required before comparison")
    if reference.sensor != "sentinel-2" or target.sensor != "sentinel-2" or model.sensor != "sentinel-2":
        raise ValueError("Radar cannot substitute for comparable optical change inputs")
    if reference.crs != target.crs or not np.allclose(reference.transform, target.transform, rtol=0, atol=1e-9):
        raise ValueError("Change inputs must be coregistered on the same grid")
    policy = model.publication_policy
    required = ("change_threshold", "min_area_m2", "max_alignment_rmse_pixels", "minimum_observable_fraction")
    if any(policy.get(key) is None for key in required):
        raise ValueError("Measured alignment, area, observability and calibration thresholds are required")
    if (any(not math.isfinite(policy[key]) for key in required)
            or not 0 < policy["change_threshold"] < 1
            or not 0 < policy["minimum_observable_fraction"] <= 1
            or policy["min_area_m2"] <= 0 or policy["max_alignment_rmse_pixels"] < 0):
        raise ValueError("Invalid change comparison thresholds")
    rmse = target.metadata.get("alignment_rmse_pixels")
    if (rmse is None or not math.isfinite(rmse) or rmse > policy["max_alignment_rmse_pixels"]
            or target.metadata.get("radiometric_comparable") is not True):
        raise ValueError("Input alignment or radiometric comparability is insufficient")
    from puertogt.preprocess import read_model_input
    before, after = read_model_input(reference), read_model_input(target)
    if before.shape != after.shape or max(before.shape[-2:]) > 512:
        raise ValueError("Change inputs must share a bounded grid of at most 512 pixels")
    shape = before.shape[-2:]
    static = _mask(reference, "static_area", shape) & _mask(target, "static_area", shape)
    valid = static & _mask(reference, "observable", shape) & _mask(target, "observable", shape)
    if not static.any() or valid.sum() / static.sum() < policy["minimum_observable_fraction"]:
        raise ValueError("Insufficient observable static area")
    for bundle in (reference, target):
        for key in ("vessels", "temporary"):
            if key in bundle.mask_paths:
                valid &= ~_mask(bundle, key, shape)
    if valid.sum() / static.sum() < policy["minimum_observable_fraction"]:
        raise ValueError("Insufficient observable area after excluding transient objects")
    if not reference.observation_id or not target.observation_id:
        raise ValueError("Compared images must have registered observation IDs")
    captured = [datetime.fromisoformat(bundle.metadata["captured_at"].replace("Z", "+00:00")) for bundle in (reference, target)]
    t0 = TemporalEvidence(reference.observation_id, captured[0], False)
    t1 = TemporalEvidence(target.observation_id, captured[1], True)
    if t1.captured_at <= t0.captured_at:
        raise ValueError("Change target must be later than its reference")
    network = load_change_model(model)
    import torch
    pad_h, pad_w = (-shape[0]) % 32, (-shape[1]) % 32
    with torch.inference_mode():
        left = torch.from_numpy(np.pad(before, ((0, 0), (0, pad_h), (0, pad_w))))[None]
        right = torch.from_numpy(np.pad(after, ((0, 0), (0, pad_h), (0, pad_w))))[None]
        probabilities = torch.sigmoid(network(left, right))[0, 0, :shape[0], :shape[1]].numpy()
    if not np.isfinite(probabilities[valid]).all():
        raise ValueError("Change model returned non-finite probabilities")
    changed = ((probabilities >= policy["change_threshold"]) & valid).astype(np.uint8)
    from pyproj import Transformer
    from rasterio import Affine
    from rasterio.features import shapes
    from shapely.geometry import mapping
    from shapely.geometry import shape as geometry_shape
    from shapely.ops import transform as project_geometry
    wgs84 = Transformer.from_crs(target.crs, "EPSG:4326", always_xy=True)
    events = []
    for polygon, value in shapes(changed, mask=changed.astype(bool), transform=Affine(*target.transform)):
        if value != 1:
            continue
        geometry = project_geometry(wgs84.transform, geometry_shape(polygon))
        zone = min(60, max(1, int((geometry.centroid.x + 180) // 6) + 1))
        metric = Transformer.from_crs("EPSG:4326", f"EPSG:{32600 + zone if geometry.centroid.y >= 0 else 32700 + zone}", always_xy=True)
        area = project_geometry(metric.transform, geometry).area
        if area < policy["min_area_m2"]:
            continue
        event = track_change(t0, [t1], geometry=mapping(geometry), model_id=model.model_id)
        event.history.append({"area_m2": area, "alignment_rmse_pixels": rmse,
                              "observable_fraction": float(valid.sum() / static.sum()),
                              "quality": policy.get("status", "experimental"), "cause": "not_classified"})
        events.append(event)
    return events
