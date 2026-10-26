"""Prepare or explicitly fetch bounded GFW port-visit references for TRAIN."""

import argparse
import getpass
import json
import sys
import warnings
from pathlib import Path

from puertogt.gfw_reference import (
    DEFAULT_LIMITS,
    GFWError,
    configure_token,
    fetch,
    freeze_plan,
    status,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    configure = commands.add_parser("configure", help="Store a token entered privately with getpass")
    configure.add_argument("--replace", action="store_true")
    commands.add_parser("status", help="Inspect local credential state without network access")
    plan = commands.add_parser("plan", help="Freeze a metadata-only TRAIN plan without network access")
    plan.add_argument("--start", default="2025-01-01")
    plan.add_argument("--end-exclusive", default="2025-07-01")
    plan.add_argument("--page-size", type=int, default=DEFAULT_LIMITS["page_size"])
    plan.add_argument("--max-pages", type=int, default=DEFAULT_LIMITS["max_pages"])
    plan.add_argument("--max-events", type=int, default=DEFAULT_LIMITS["max_events"])
    plan.add_argument("--max-total-bytes", type=int, default=DEFAULT_LIMITS["max_total_bytes"])
    plan.add_argument("--max-seconds", type=int, default=DEFAULT_LIMITS["max_seconds"])
    download = commands.add_parser("fetch", help="Explicitly query GFW using a verified frozen plan")
    download.add_argument("--plan", type=Path, required=True)
    download.add_argument("--sha256", required=True)
    args = parser.parse_args(argv)
    ports = args.workspace / "config/ports.geojson"
    try:
        if args.command == "configure":
            if not sys.stdin.isatty():
                raise GFWError("terminal_required", "Configure requires an interactive terminal; no stdin token.")
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                token = getpass.getpass("GFW token (hidden): ")
            result = configure_token(args.workspace, token, replace=args.replace)
        elif args.command == "status":
            result = status(args.workspace)
        elif args.command == "plan":
            result = freeze_plan(args.workspace, ports, start=args.start, end=args.end_exclusive,
                                 limits={**DEFAULT_LIMITS, "page_size": args.page_size,
                                         "max_pages": args.max_pages, "max_events": args.max_events,
                                         "max_total_bytes": args.max_total_bytes,
                                         "max_seconds": args.max_seconds})
        else:
            result = fetch(args.workspace, args.plan, args.sha256, ports)
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result["status"] == "stopped" or result.get("configured") is False else 0
    except GFWError as exc:
        print(json.dumps({"status": exc.code, "message": str(exc)}))
        return 2
    except (OSError, ValueError, KeyError, getpass.GetPassWarning):
        print(json.dumps({"status": "local_error", "message": "Local input or storage failed; no details logged."}))
        return 2
    except (KeyboardInterrupt, EOFError):
        print(json.dumps({"status": "cancelled"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
