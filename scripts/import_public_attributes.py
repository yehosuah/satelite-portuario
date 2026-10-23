#!/usr/bin/env python3
"""Materialize pinned public attribute contexts with bounded, resumable I/O."""

import argparse
import json
from contextlib import nullcontext
from pathlib import Path

from puertogt.ml.public_attributes import (
    cache_public_features,
    finalize_public_attributes,
    materialize_public_attributes,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["materialize", "finalize", "cache"])
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--selection-sha256", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--io-workers", type=int, choices=[1, 4], default=1)
    parser.add_argument("--performance-amendment", type=Path)
    parser.add_argument("--amendment-sha256")
    parser.add_argument("--history-dir", type=Path, default=Path("data/history/full-v1"))
    parser.add_argument("--encoder", type=Path, default=Path("config/models/s2.yaml"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    if args.command == "materialize":
        from puertogt.history import file_lock
        from puertogt.ml.public_attribute_transport import pooled_public_ranges

        if args.io_workers > 1 and not (args.history_dir / "PAUSE").exists():
            raise ValueError("Pause the historical campaign before using four public I/O workers")
        guard = file_lock(args.history_dir / "campaign.lock") if args.io_workers > 1 else nullcontext()
        with guard, pooled_public_ranges(max_connections=args.io_workers):
            result = materialize_public_attributes(args.selection, args.selection_sha256, args.destination,
                                                   max_samples=args.max_samples, io_workers=args.io_workers,
                                                   performance_amendment=args.performance_amendment,
                                                   amendment_sha256=args.amendment_sha256)
    elif args.command == "finalize":
        result = {"datasets": str(finalize_public_attributes(args.selection, args.selection_sha256,
                                                               args.destination))}
    else:
        from puertogt.cli import read_model
        from puertogt.ml.public_attributes import load_selection

        load_selection(args.selection, args.selection_sha256)
        dataset = json.loads((args.destination / "datasets/train/manifest.json").read_text())
        if dataset.get("selection_sha256") != args.selection_sha256:
            raise ValueError("Feature dataset differs from the frozen public selection")
        encoder = read_model(args.encoder, args.data_dir)
        manifest = cache_public_features(args.destination / "datasets/train", encoder,
                                         args.destination / "training-features",
                                         heavy_lock=args.data_dir / ".heavy-worker.lock")
        result = {"manifest": str(manifest)}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
