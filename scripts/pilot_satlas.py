#!/usr/bin/env python3
"""Exercise the real Satlas change adapter without creating trained change weights.

After ``uv sync --extra ml``, run:
  uv run --no-sync python scripts/pilot_satlas.py --download

This compares the same numeric acquisition to itself to check wiring. The decoder
is random, labels used to check the loss are synthetic, and no model is exported.
"""

import argparse
import json
import platform
import resource
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
import torch
import yaml

from puertogt.assets import download_asset, file_hash
from puertogt.contracts import RasterBundle
from puertogt.ml.change import create_satlas_change_model, masked_change_loss
from puertogt.preprocess import read_model_input


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="config/models/change.yaml")
    parser.add_argument("--bundle", default="data/pilot/stc-prepared/bundle.json")
    parser.add_argument("--report", default="reports/pilot/satlas-smoke.json")
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    config = yaml.safe_load(Path(args.model).read_text())
    metadata = config["metadata"]
    checkpoint = Path(metadata["encoder_checkpoint"])
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "real_pretrained_encoder_and_random_decoder_operability_only",
              "model_identifier": metadata["encoder_identifier"],
              "decoder_state": "random_untrained_not_exported", "device": "cpu",
              "torch": torch.__version__, "cpu_threads": 4, "seed": 0,
              "radiometric_reproduction_of_pretraining": False,
              "input_adaptation": metadata["input_adaptation"]}
    if args.download:
        started = time.monotonic()
        report["download"] = download_asset(
            metadata["encoder_checkpoint_url"], checkpoint,
            expected_sha256=metadata["encoder_sha256"], max_bytes=250000000,
        )
        report["download"]["seconds"] = time.monotonic() - started
    torch.set_num_threads(4)
    torch.manual_seed(0)
    bundle = RasterBundle.model_validate_json(Path(args.bundle).read_text())
    image = read_model_input(bundle)[:, 128:384, 128:384].copy()
    if image.shape != (9, 256, 256):
        raise ValueError("smoke requires a 512-pixel source with the selected 256-pixel numeric crop")
    with rasterio.open(bundle.mask_paths["observable"]) as source:
        valid = source.read(1)[128:384, 128:384] > 0
    before = torch.from_numpy(image)[None]
    after = before.clone()
    started = time.monotonic()
    network = create_satlas_change_model(
        encoder_checkpoint=checkpoint, encoder_sha256=metadata["encoder_sha256"],
        decoder_channels=metadata["decoder_channels"],
    ).eval()
    report["load_seconds"] = time.monotonic() - started
    started = time.monotonic()
    with torch.inference_mode():
        features = network.encoder(before)
    report["encoder_seconds"] = time.monotonic() - started
    report["feature_shapes"] = [list(value.shape) for value in features]
    report["encoder_outputs_finite"] = all(bool(torch.isfinite(value).all()) for value in features)
    started = time.monotonic()
    # Keep gradients only for the synthetic loss operability check below.
    logits = network(before, after)
    report["pair_forward_seconds"] = time.monotonic() - started
    report["output_shape"] = list(logits.shape)
    report["output_finite"] = bool(torch.isfinite(logits).all())
    if logits.shape != (1, 1, 256, 256) or not report["output_finite"]:
        raise RuntimeError("change adapter returned an invalid output")
    if report["feature_shapes"] != [[1, 96, 64, 64], [1, 192, 32, 32], [1, 384, 16, 16], [1, 768, 8, 8]]:
        raise RuntimeError("real Satlas feature pyramid differs from decoder recipe")
    synthetic_targets = torch.zeros_like(logits)
    synthetic_targets[:, :, 96:160, 96:160] = 1
    valid_mask = torch.from_numpy(valid.copy())[None, None]
    valid_mask[:, :, :16] = False
    started = time.monotonic()
    loss = masked_change_loss(logits, synthetic_targets, valid_mask)
    loss.backward()
    encoder_has_gradients = any(p.grad is not None for p in network.encoder.parameters())
    decoder_gradients = [p.grad for name, p in network.named_parameters() if not name.startswith("encoder.") and p.grad is not None]
    gradients_finite = bool(decoder_gradients) and all(bool(torch.isfinite(g).all()) for g in decoder_gradients)
    if encoder_has_gradients or not gradients_finite:
        raise RuntimeError("frozen encoder or decoder gradient contract failed")
    report["synthetic_loss_check"] = {
        "labels": "synthetic square; not reviewed observations or change evidence",
        "loss": float(loss.detach()), "backward_seconds": time.monotonic() - started,
        "encoder_has_gradients": encoder_has_gradients,
        "decoder_gradients_finite": gradients_finite, "optimizer_step_performed": False,
        "synthetic_loss_mask_fraction": float(valid_mask.float().mean()),
        "model_weights_exported": False,
    }
    report["input"] = {"bundle": str(args.bundle), "scene_id": bundle.metadata["scene_id"],
                       "pair_is_same_acquisition": True, "shape": list(before.shape),
                       "pixel_window": [128, 128, 256, 256],
                       "min_per_band": image.min(axis=(1, 2)).tolist(),
                       "max_per_band": image.max(axis=(1, 2)).tolist(),
                       "observable_fraction": float(np.mean(valid)),
                       "bundle_sha256": file_hash(Path(args.bundle))}
    report["encoder_checkpoint_sha256"] = file_hash(checkpoint)
    report["encoder_checkpoint_bytes"] = checkpoint.stat().st_size
    report["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if platform.system() == "Darwin" else 1024)
    report["success"] = True
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
