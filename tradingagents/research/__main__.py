"""Data preparation in Actions; local Codex debate orchestration and evidence validation."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from tradingagents.dataflows.errors import VendorError
from tradingagents.dataflows.taiwan.provider import TaiwanMarketDataProvider

from .bundle import build_bundle, check_dates, fingerprint, reject_secret
from .debate import STAGES, load_json, load_run, register_agent, start_run, stop, submit, write_json


def main(argv=None, provider=None):
    parser = argparse.ArgumentParser(description="Taiwan research: ChatGPT-auth Codex only, no orders")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Fetch once and create evidence-only run folders")
    prepare.add_argument("--symbols", default=os.getenv("RESEARCH_SYMBOLS", "2330,3017,3231"))
    prepare.add_argument("--start", default=os.getenv("RESEARCH_START"), required=not os.getenv("RESEARCH_START"))
    prepare.add_argument("--as-of", default=os.getenv("RESEARCH_AS_OF"), required=not os.getenv("RESEARCH_AS_OF"))
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--require-token", action="store_true")
    imported = commands.add_parser("from-snapshot", help="Create a run from an already saved snapshot")
    imported.add_argument("--snapshot", type=Path, required=True)
    for name in ("from-snapshot", "status", "register-agent", "submit", "stop", "run"):
        item = imported if name == "from-snapshot" else commands.add_parser(name)
        item.add_argument("--run-dir", type=Path, required=True)
        if name == "register-agent":
            item.add_argument("--role", choices=("bull", "bear", "risk"), required=True)
            item.add_argument("--agent-id", required=True)
        elif name == "submit":
            item.add_argument("--stage", choices=STAGES, required=True)
            item.add_argument("--file", type=Path, required=True)
        elif name == "stop":
            item.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            token = os.getenv("FINMIND_TOKEN", "")
            if args.require_token and not token:
                raise ValueError("FINMIND_TOKEN Secret missing; Sponsor data preparation stopped")
            check_dates(args.start, args.as_of)
            symbols = list(dict.fromkeys(args.symbols.replace(" ", ",").split(",")))
            symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
            if not symbols or len(symbols) > 10 or any(not re.fullmatch(r"\d{4,6}(\.TW|\.TWO)?", s) for s in symbols):
                raise ValueError("Use at most ten Taiwan stock codes")
            provider = provider or TaiwanMarketDataProvider()
            summary = []
            for symbol in symbols:
                try:
                    snapshot = provider.snapshot(symbol, args.start, args.as_of)
                    reject_secret(snapshot, token)
                    bundle = build_bundle(snapshot)
                    folder = args.output_dir / f"{symbol}-{args.as_of}"
                    start_run(bundle, folder)
                    # Original dates, raw excluded rows, and full provenance are kept for audit.
                    write_json(folder / "snapshot.json", snapshot)
                    summary.append({"symbol": symbol, "status": "prepared", "path": folder.name,
                                    "bundle_sha256": fingerprint(bundle),
                                    "critical_data_missing": bundle["requires_insufficient_data"]})
                except (VendorError, ValueError, KeyError, TypeError):
                    summary.append({"symbol": symbol, "status": "unavailable"})
            args.output_dir.mkdir(parents=True, exist_ok=True)
            write_json(args.output_dir / "preparation.json", {"as_of": args.as_of, "runs": summary})
            print(json.dumps(summary, ensure_ascii=False))
            return 0 if all(item["status"] == "prepared" for item in summary) else 1
        if args.command == "from-snapshot":
            snapshot = load_json(args.snapshot)
            reject_secret(snapshot, os.getenv("FINMIND_TOKEN", ""))
            start_run(build_bundle(snapshot), args.run_dir)
        elif args.command == "register-agent":
            register_agent(args.run_dir, args.role, args.agent_id)
        elif args.command == "submit":
            submit(args.run_dir, args.stage, load_json(args.file))
        elif args.command == "stop":
            stop(args.run_dir, args.reason)
        elif args.command == "run":
            from .runner import launch

            return launch(args.run_dir)
        state, _ = load_run(args.run_dir)
        print(json.dumps({"status": state["status"], "completed": list(state["completed"]),
                          "stop_reason": state["stop_reason"],
                          "bundle_sha256": state["bundle_sha256"]}, ensure_ascii=False))
        return 0
    except (VendorError, ValueError, KeyError, TypeError, OSError):
        # Do not echo input JSON, subprocess logs, paths supplied by a model, or credentials.
        print("Research operation failed: check dates, required fields, evidence references, and stage prerequisites.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
