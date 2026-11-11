import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from tests.taiwan.test_calendar_recovery import archive
from tradingagents.dataflows.taiwan.extensions import ADJUSTED, normalize
from tradingagents.dataflows.taiwan.provider import PRICE, TaiwanMarketDataProvider
from tradingagents.research.research_outcomes import (
    financial_results,
    market_results,
    verify_actual_quote,
)

NOW = datetime(2026, 11, 16, tzinfo=UTC)
DAYS = ["2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]


def capture(stock, benchmark=False):
    datasets = {}
    for dataset in (PRICE, ADJUSTED):
        rows = []
        for i, day in enumerate(DAYS):
            close = 100 + (i if benchmark else 2 * i)
            raw = {"stock_id": stock, "date": day, "open": 100, "max": close + 1,
                   "min": 99, "close": close, "Trading_Volume": 1000}
            convert = normalize if dataset == ADJUSTED else TaiwanMarketDataProvider._normalize
            period, values, units = convert(dataset, raw)
            rows.append({"data_date": day, "raw": raw, "period": period, "values": values, "units": units,
                         "retrieved_at": NOW.isoformat(), "published_at": None})
        datasets[dataset] = {"records": rows}
    return {
        "instrument": {"stock_id": stock, "exchange": "twse"}, "as_of": "2026-11-16",
        "datasets": {PRICE: datasets[PRICE]}, "extensions": {ADJUSTED: datasets[ADJUSTED]},
        "market_calendar": {"status": "ok", "exchange": "twse", "start": "2026-11-06",
                            "end": "2026-11-15", "sessions": DAYS,
                            "archives": [archive(dates=DAYS, captured=NOW.isoformat())]},
    }


def record():
    return {
        "research_id": "synthetic", "scoring_status": "pending", "stock_id": "1216",
        "snapshot_sha256": "bound", "recorded_at": "2026-11-07T12:00:00+08:00",
        "decision_conditions": {"status": "conditional_candidate", "market_session": DAYS[0],
                                "technical": {"invalidation_level": 95},
                                "hypothetical": {"target_twd": 110, "invalidation_twd": 94,
                                                 "entry_cost_bps": 10, "exit_cost_bps": 40}},
        "conditional_valuation": {"periods": ["2026-Q4"]},
    }


class ResearchOutcomeTests(unittest.TestCase):
    def test_mature_five_day_and_pending_twenty_day(self):
        rows = market_results(record(), {"1216": capture("1216"), "0050": capture("0050", True)}, "0050", NOW)
        self.assertEqual([r["status"] for r in rows], ["observed", "pending"])
        self.assertAlmostEqual(rows[0]["excess_return_pct"], 5)
        self.assertAlmostEqual(rows[0]["mfe_pct"], 11)
        self.assertAlmostEqual(rows[0]["mae_pct"], -1)
        self.assertLess(rows[0]["net_cost_sensitivity_pct"], rows[0]["stock_return_pct"])
        self.assertTrue(rows[0]["target_close_observed"])
        self.assertIsNone(rows[0]["foreign_next_five_net_shares"])

    def test_missing_bar_or_late_archive_is_not_scored(self):
        stock, benchmark = capture("1216"), capture("0050", True)
        index = {"1216": stock, "0050": benchmark}
        benchmark["extensions"][ADJUSTED]["records"].pop()
        self.assertEqual(market_results(record(), index, "0050", NOW)[0]["reason"], "missing_aligned_adjusted_bars")
        research = {**record(), "recorded_at": "2026-11-09T09:00:00+08:00"}
        self.assertEqual(market_results(research, index, "0050", NOW)[0]["reason"], "archive_after_first_session_open")

    def test_adjustment_change_disables_raw_stop_test(self):
        stock = capture("1216")
        row = stock["datasets"][PRICE]["records"][-1]
        row["raw"]["close"] = row["values"]["close"] = 101
        result = market_results(record(), {"1216": stock, "0050": capture("0050", True)}, "0050", NOW)[0]
        self.assertEqual(result["status"], "observed")
        self.assertIsNone(result["invalidation_close_observed"])

    def test_financial_actual_requires_early_issue_and_original_numeric_quote(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "original.txt"
            raw = "合併第四季稀釋每股盈餘 1.20".encode()
            source.write_bytes(raw)
            model = {"scenarios": {"base": {"quarters": [{"period": "2026-Q4", "eps_twd": 1.30}]}}}
            (root / "forward-estimate.json").write_text(json.dumps(model), encoding="utf8")
            row = {"stock_id": "1216", "metric": "eps", "basis": "diluted_single_quarter",
                   "period": "2026-Q4", "value": 1.20, "unit": "TWD/share",
                   "published_at": "2027-02-01T10:00:00+08:00", "captured_at": "2027-02-01T11:00:00+08:00",
                   "revision_id": "v1", "vintage_kind": "initial", "revision_policy": "first_official_release",
                   "source_url": "https://mops.twse.com.tw/synthetic", "source_locator": "p1",
                   "source_sha256": hashlib.sha256(raw).hexdigest(), "source_file": str(source),
                   "reviewer": "synthetic fixture", "source_quote": raw.decode(), "raw_value": "1.20",
                   "source_page": 1, "multiplier": 1}
            for key in ("initial_publication_verified", "source_value_reviewed",
                        "initial_publication_history_verified", "official_source_reviewed"):
                row[key] = True
            when = datetime(2027, 2, 2, tzinfo=UTC)
            result = financial_results(root, record(), [row], when)
            self.assertAlmostEqual(result["resolved"][0]["error"], 0.10)
            self.assertFalse(financial_results(root, record(), [row], NOW)["resolved"])
            with self.assertRaisesRegex(ValueError, "differs from original"):
                verify_actual_quote({**row, "value": 12.0})
