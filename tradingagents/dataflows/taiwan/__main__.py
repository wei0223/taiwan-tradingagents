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
    parser.add_argument("--extended", action="store_true")
    parser.add_argument("--news-days", type=int, default=3)
    parser.add_argument("--financial-period-review", type=Path,
                        help="Reviewed per-field observations and original documents; dates alone never certify quarters")
    args = parser.parse_args(argv)
    try:
        if provider is None:
            resolver = None
            if args.mops_publications or args.extended:
                from .publications import MopsPublicationResolver

                resolver = MopsPublicationResolver(args.start, args.as_of)
            if args.extended:
                from .calendar import OfficialCalendarProvider
                from .extensions import ExtendedTaiwanProvider

                provider = ExtendedTaiwanProvider(publication_resolver=resolver,
                                                  calendar_provider=OfficialCalendarProvider(), news_days=args.news_days)
            else:
                provider = TaiwanMarketDataProvider(publication_resolver=resolver)
        result = provider.snapshot(args.symbol, args.start, args.as_of)
        if args.financial_period_review:
            from .periods import refresh_snapshot

            result = refresh_snapshot(result, json.loads(args.financial_period_review.read_text(encoding="utf8")))
    except (VendorError, ValueError) as exc:
        print(json.dumps({"status": "unavailable", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    groups = [result["datasets"], result.get("extensions", {})]
    return 0 if all(d["status"] == "ok" for group in groups for d in group.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
