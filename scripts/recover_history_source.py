#!/usr/bin/env python3
"""Recover a verified original S2 representation only at a paused boundary."""

import argparse
import json
from pathlib import Path

from puertogt.history import HistoryLedger, HistoryLimits
from puertogt.history_recovery import recover_compatible_source
from puertogt.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-dir", type=Path, default=Path("data/history/full-v1"))
    parser.add_argument("--database", type=Path, default=Path("data/catalog.sqlite"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    limits = HistoryLimits(**json.loads((args.history_dir / "limits.json").read_text()))
    ledger = HistoryLedger(args.history_dir, limits)
    result = recover_compatible_source(Store(args.database, args.data_dir), ledger, args.candidate,
                                       candidate_sha256=args.candidate_sha256,
                                       plan_sha256=args.plan_sha256, reason=args.reason)
    print(json.dumps({"status": result["status"], "acquisition_key": result["acquisition_key"],
                      "plan_sha256": result["plan_sha256"], "candidate_sha256": result["candidate_sha256"],
                      "sources_sha256": result["sources_sha256"], "ledger": ledger.summary()}, indent=2))


if __name__ == "__main__":
    main()
