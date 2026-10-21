"""Pinned Ai2/rslearn detector adapter; numeric inputs and georeferenced proposals."""

import hashlib
import time
from pathlib import Path

import numpy as np
import rasterio
import yaml
from pyproj import Transformer
from rasterio import Affine

from puertogt.assets import file_hash
from puertogt.contracts import ModelManifest, Prediction, RasterBundle
from puertogt.preprocess import read_model_input


def load_manifest(path: Path) -> ModelManifest:
    return ModelManifest.model_validate(yaml.safe_load(Path(path).read_text()))


class Detector:
    """A single loaded detector, reusable across a sequence of bounded crops."""

    def __init__(self, manifest: ModelManifest, model, device: str):
        self.manifest = manifest
        self.model = model
        self.device = device

    def _context(self, image: np.ndarray):
        import torch
        from rslearn.train.model_context import ModelContext, RasterImage

        tensor = torch.from_numpy(np.ascontiguousarray(image)).to(self.device)
        if self.manifest.sensor == "sentinel-1":
            tensor = tensor.reshape(2, 2, *tensor.shape[-2:]).permute(1, 0, 2, 3)
        else:
            tensor = tensor[:, None]
        return ModelContext(inputs=[{"image": RasterImage(tensor)}], metadatas=[])

    def raw_predict(self, image: np.ndarray) -> dict[str, np.ndarray]:
        """Return boxes/scores in crop coordinates, without an infrastructure filter."""
        import torch

        self.model.eval()
        with torch.inference_mode():
            result = list(self.model(self._context(image)).outputs)[0]["detect"]
        return {key: value.detach().cpu().numpy() for key, value in result.items()}

    def features(self, image: np.ndarray):
        """Frozen FPN tensors at strides 4/8/16/32; 128 S2 or 256 S1 channels."""
        import torch

        self.model.eval()
        context = self._context(image)
        with torch.inference_mode():
            features = self.model.encoder[0](context)
            for module in self.model.encoder[1:]:
                features = module(features, context)
        return [feature.detach() for feature in features.feature_maps]

    def make_optimizer(self, *, learning_rate: float = 2e-5):
        """Freeze the backbone and build a reusable FPN/detector AdamW optimizer."""
        import torch

        if not 0 < learning_rate < 1:
            raise ValueError("learning_rate must be between 0 and 1")
        for parameter in self.model.encoder[0].parameters():
            parameter.requires_grad_(False)
        return torch.optim.AdamW((p for p in self.model.parameters() if p.requires_grad), lr=learning_rate)

    def frozen_train_step(
        self, image: np.ndarray, boxes: np.ndarray, *, optimizer=None, learning_rate: float = 2e-5
    ) -> dict:
        """One measured supervised head/FPN step; caller supplies reference labels.

        This establishes resource feasibility, never validation or local accuracy.
        It deliberately leaves the backbone frozen and uses only supplied boxes.
        """
        import torch

        for parameter in self.model.encoder[0].parameters():
            parameter.requires_grad_(False)
        self.model.train()
        self.model.encoder[0].eval()
        trainable = [p for p in self.model.parameters() if p.requires_grad]
        if not trainable:
            raise ValueError("no trainable detector parameters")
        before = trainable[0].detach().clone()
        optimizer = optimizer or self.make_optimizer(learning_rate=learning_rate)
        box_tensor = torch.as_tensor(boxes, dtype=torch.float32, device=self.device).reshape(-1, 4)
        targets = [{"detect": {"boxes": box_tensor,
                               "labels": torch.ones(len(box_tensor), dtype=torch.int64,
                                                    device=self.device)}}]
        started = time.monotonic()
        optimizer.zero_grad(set_to_none=True)
        output = self.model(self._context(image), targets)
        loss = sum(output.loss_dict.values())
        if not torch.isfinite(loss):
            raise ValueError("non-finite supervised training loss")
        loss.backward()
        optimizer.step()
        if self.device == "mps":
            torch.mps.synchronize()
        changed = not torch.equal(before, trainable[0].detach())
        if not changed:
            raise RuntimeError("training step did not update the detector")
        self.model.eval()
        return {
            "loss": float(loss.detach().cpu()),
            "losses": {key: float(value.detach().cpu()) for key, value in output.loss_dict.items()},
            "seconds": time.monotonic() - started,
            "encoder_frozen": all(not p.requires_grad for p in self.model.encoder[0].parameters()),
            "updated_parameters": changed,
            "reference_boxes": len(boxes),
            "scope": "operability_only_not_local_validation",
        }


def load_detector(model: ModelManifest, *, device: str = "cpu") -> Detector:
    """Load strictly matching weights only after validating their SHA256."""
    if model.task != "detection":
        raise ValueError("a detection manifest is required")
    if not model.weights_path or not model.weights_sha256 or not model.code_commit:
        raise ValueError("checkpoint path, SHA256 and code commit must be pinned")
    path = Path(model.weights_path)
    if file_hash(path) != model.weights_sha256:
        raise ValueError("checkpoint SHA256 mismatch")
    if device not in {"cpu", "mps"}:
        raise ValueError("supported pilot devices are cpu and mps")

    import torch
    from rslearn.models.faster_rcnn import FasterRCNN
    from rslearn.models.fpn import Fpn
    from rslearn.models.multitask import MultiTaskModel
    from rslearn.models.swin import Swin

    if device == "mps" and not torch.backends.mps.is_available():
        raise ValueError("MPS is not available in this runtime")
    if model.sensor == "sentinel-2":
        encoder = Swin(pretrained=False, input_channels=9, output_layers=[1, 3, 5, 7])
        fpn = Fpn(in_channels=[128, 256, 512, 1024], out_channels=128)
        channels = 128
    else:
        from rslearn.models.simple_time_series import SimpleTimeSeries

        encoder = SimpleTimeSeries(
            encoder=Swin(pretrained=False, input_channels=2, output_layers=[1, 3, 5, 7]),
            num_timesteps_per_forward_pass=1, groups=[[0], [1]],
        )
        fpn = Fpn(in_channels=[256, 512, 1024, 2048], out_channels=256)
        channels = 256
    network = MultiTaskModel(
        encoder=[encoder, fpn], decoders={"detect": [FasterRCNN(
            downsample_factors=[4, 8, 16, 32], num_channels=channels, num_classes=2,
            anchor_sizes=[[32], [64], [128], [256]],
        )]},
    )
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    state = checkpoint.get("state_dict", checkpoint)
    state = {key.removeprefix("model."): value for key, value in state.items()}
    network.load_state_dict(state, strict=True)
    network.to(device).eval()
    return Detector(model, network, device)


def detect(
    bundle: RasterBundle, model: ModelManifest, *, device: str = "cpu", detector: Detector | None = None
) -> list[Prediction]:
    """Detect vessel proposals in WGS84; threshold is not an accuracy assertion.

    Sliding crops retain acquisition identity. The marine-infrastructure deletion
    from upstream (50 m S2, 200 m S1) is not applied; all results remain experimental
    proposals until a separate local validation authorizes publication.
    """
    image = read_model_input(bundle, model)
    detector = detector or load_detector(model, device=device)
    if (detector.manifest.model_id != model.model_id or detector.manifest.version != model.version
            or detector.manifest.weights_sha256 != model.weights_sha256):
        raise ValueError("loaded detector does not match the requested manifest")
    size = 512
    height, width = image.shape[-2:]
    if height > 4096 or width > 4096:
        raise ValueError("detect requires a bounded crop no larger than 4096 pixels")

    def offsets(length: int) -> list[int]:
        return sorted(set(range(0, max(1, length - size + 1), 460)) | {max(0, length - size)})

    threshold = float(model.publication_policy.get("score_threshold", 0.8))
    candidates = []
    mask_path = bundle.mask_paths.get("observable") or bundle.mask_paths.get("potentially_observable")
    usable_pixels = None
    if mask_path:
        with rasterio.open(mask_path) as src:
            if src.shape != (height, width) or str(src.crs) != bundle.crs or not src.transform.almost_equals(
                Affine(*bundle.transform)
            ):
                raise ValueError("observability mask does not match the numeric grid")
            usable_pixels = src.read(1) > 0
    for y in offsets(height):
        for x in offsets(width):
            crop = image[:, y:y + size, x:x + size]
            # The Swin grid works at multiples of 32; zero padding carries no new data.
            pad_h, pad_w = (-crop.shape[1]) % 32, (-crop.shape[2]) % 32
            padded = np.pad(crop, ((0, 0), (0, pad_h), (0, pad_w)))
            output = detector.raw_predict(padded)
            for box, score, label in zip(output["boxes"], output["scores"], output["labels"]):
                if score < threshold or label != 1:
                    continue
                box = box + np.array([x, y, x, y])
                cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                if 0 <= cx < width and 0 <= cy < height:
                    if usable_pixels is None or usable_pixels[int(cy), int(cx)]:
                        candidates.append((float(score), box, cx, cy))

    # Match upstream's 10-pixel center distance merge across overlapping crops.
    kept = []
    for candidate in sorted(candidates, key=lambda item: item[0], reverse=True):
        if all((candidate[2] - prev[2]) ** 2 + (candidate[3] - prev[3]) ** 2 >= 100
               for prev in kept):
            kept.append(candidate)
    transform = Affine(*bundle.transform)
    project = Transformer.from_crs(bundle.crs, "EPSG:4326", always_xy=True)
    predictions = []
    for index, (score, box, _, _) in enumerate(kept):
        x1, y1, x2, y2 = map(float, box)
        corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
        coordinates = [list(project.transform(*(transform * point))) for point in corners]
        identity = f"{bundle.observation_id or bundle.metadata.get('scene_id', '')}:{model.model_id}:{model.version}:{index}:{coordinates}"
        predictions.append(Prediction(
            prediction_id=hashlib.sha256(identity.encode()).hexdigest()[:24],
            geometry={"type": "Polygon", "coordinates": [coordinates]}, detection_score=score,
            field_quality={"detection": "experimental", "family": "not_evaluated",
                           "length_m": "not_evaluated", "width_m": "not_evaluated"},
            metadata={"model_id": model.model_id, "model_version": model.version,
                      "pixel_box": list(map(float, box)), "score_threshold": threshold,
                      "geometry_role": "detection_window_not_vessel_dimensions",
                      "infrastructure_filter_applied": False,
                      "quality_flags": bundle.metadata.get("quality_flags", [])},
        ))
    return predictions
