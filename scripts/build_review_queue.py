"""Create an immutable metadata-only TRAIN inspection proposal."""
import argparse
import json
from pathlib import Path

from puertogt.review_queue import build_review_queue, freeze_queue
from puertogt.store import Store

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--sampling", type=Path, default=Path("reports/local-review/phase2/sampling-plan.json"))
    parser.add_argument("--order", type=Path, default=Path("reports/local-review/phase2/execution-order-v11.json"))
    parser.add_argument("--ports", type=Path, default=Path("config/ports.geojson"))
    parser.add_argument("--per-stratum", type=int, default=2)
    parser.add_argument("--minimum-observable", type=float, default=.5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_review_queue(Store(args.data_dir / "catalog.sqlite", args.data_dir), args.sampling,
                                  args.order, args.ports, per_stratum=args.per_stratum,
                                  minimum_observable=args.minimum_observable)
    digest = freeze_queue(manifest, args.output)
    print(json.dumps({"queue_id": manifest["queue_id"], "sha256": digest,
                      "distribution": manifest["distribution"], "entries": len(manifest["entries"])}))
