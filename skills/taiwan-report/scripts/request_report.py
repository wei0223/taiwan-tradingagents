"""Plan one Codex report; no network, credentials, publishers or schedulers."""

import argparse
import json
import re
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

TAIPEI = timezone(timedelta(hours=8))


def create_request(symbol, repository, output_root, as_of=None, start=None, clock=None):
    if not isinstance(symbol, str) or not re.fullmatch(r"\d{4,6}(?:\.TW|\.TWO)?", symbol.strip().upper()):
        raise ValueError("Provide one Taiwan stock identifier, optionally .TW or .TWO")
    supplied = symbol.strip().upper()
    stock = supplied.split(".")[0]
    now = (clock or (lambda: datetime.now(UTC)))()
    if now.tzinfo is None:
        raise ValueError("Clock requires timezone")
    today = now.astimezone(TAIPEI).date()
    cutoff = date.fromisoformat(as_of) if as_of else today
    first = date.fromisoformat(start) if start else date(max(1, cutoff.year - 5), 1, 1)
    if not first <= cutoff <= today:
        raise ValueError("Invalid date range or future analysis date")
    repo = Path(repository).resolve()
    if not (repo / "tradingagents/research/__main__.py").is_file():
        raise ValueError("Configured Taiwan TradingAgents repository is unavailable")
    root = Path(output_root).resolve()
    # Do not place generated private data into the source checkout by default or by accident.
    if root == repo or root.is_relative_to(repo):
        raise ValueError("Keep generated reports outside the source checkout")
    name = stock + "-" + now.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    folder = root / name
    folder.mkdir(parents=True, exist_ok=False)
    record = {
        "schema_version": 1,
        "mode": "on_demand_report",
        "state": "request_created_report_not_yet_generated",
        "symbol": supplied,
        "stock_id": stock,
        "exchange": "resolve_with_provider_metadata",
        "as_of": cutoff.isoformat(),
        "start": first.isoformat(),
        "created_at": now.astimezone(UTC).isoformat(),
        "repository": str(repo),
        "request_dir": str(folder),
        "data_dir": str(folder / "data"),
        "report_dir": str(folder / "report"),
        "workflow": {"repository": "wei0223/taiwan-tradingagents", "file": "taiwan-prepare.yml", "ref": "main",
            "inputs": {"symbols": supplied, "start": first.isoformat(), "as_of": cutoff.isoformat(),
                       "mops_publications": True, "extended": True}},
        "analysts": ["fundamental", "technical", "institutional"],
        "forecast_mode": "factset_public_annual_eps_median",
        "valuation_mode": "conditional_unless_independently_qualified",
        "source_review_required": True,
        "upgrade_extension_defaults": {"stock_id": stock, "as_of": cutoff.isoformat(), "one_shot": True, "fixed_pipeline": True, "agent_protocol": "taiwan-eight-stage-v2"},
        "technical_minimum_sessions": 1000,
        "company_sop_version": "company-research-v1",
        "backtest_required": False,
        "publish_forecasts": False,
        "create_or_resume_monitor": False,
        "paid_llm_api": False,
        "orders": False,
    }
    path = folder / "request.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--as-of")
    parser.add_argument("--start")
    args = parser.parse_args(argv)
    config_path = Path(__file__).with_name("project.json")
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    repo = args.repository or config.get("repository")
    output = args.output_root or config.get("output_root")
    if not repo or not output:
        parser.error("Set project.json or provide --repository and --output-root")
    try:
        path = create_request(args.symbol, repo, output, args.as_of, args.start)
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + "\n")
    print(json.dumps({"request": str(path), "report_generated": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
