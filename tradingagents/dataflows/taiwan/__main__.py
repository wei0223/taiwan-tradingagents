"""Data-only Taiwan CLI. Does not initialize any LLM client."""

import argparse
import json
import sys
from pathlib import Path

from tradingagents.dataflows.errors import VendorError

from .provider import TaiwanMarketDataProvider


def main(argv=None, provider=None):
    parser = argparse.ArgumentParser(description="Taiwan TradingAgents: data only, no LLM or orders")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mops-publications", action="store_true")
    args = parser.parse_args(argv)
    try:
        if provider is None:
            resolver = None
            if args.mops_publications:
                from .publications import MopsPublicationResolver

                resolver = MopsPublicationResolver(args.start, args.as_of)
            provider = TaiwanMarketDataProvider(publication_resolver=resolver)
        result = provider.snapshot(args.symbol, args.start, args.as_of)
    except (VendorError, ValueError) as exc:
        print(json.dumps({"status": "unavailable", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0 if all(d["status"] == "ok" for d in result["datasets"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
