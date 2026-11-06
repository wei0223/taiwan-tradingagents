"""Regression coverage for calculated research evidence and presentation."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tradingagents.research.eight_stage import put, refs, validate_cross_review
from tradingagents.research.reader_research import build, short_term_check
from tradingagents.research.research_questions import (
    analyst_paragraphs,
    answer,
    display_prose,
    eps_comparison,
    flow_comparison,
    moving_averages,
)


class ResearchQuestionsTests(unittest.TestCase):
    def test_trend_retains_all_levels_and_excludes_long_ma_from_short_count(self):
        indicators = {
            f"SMA_{n}": [value + 1, value]
            for n, value in ((5, 105), (10, 106), (20, 107), (60, 110), (200, 90))
        }
        raw = {
            "dates": ["2026-11-03", "2026-11-04"],
            "bars": [{}, {"close": 100}],
            "indicators": indicators,
        }
        result = moving_averages({"status": "ready", "price_series": {"raw": raw}})
        self.assertEqual((result["below"], result["count"]), (4, 4))
        self.assertEqual(result["averages"][-1]["window"], 200)
        self.assertGreater(result["averages"][-1]["gap_pct"], 0)
        self.assertEqual(result["averages"][2]["change"], -1)

    def test_eps_uses_reviewed_same_quarter_before_cutoff(self):
        common = {
            "metric": "diluted_eps",
            "basis": "single_quarter",
            "scope": "consolidated",
            "unit": "TWD/share",
            "source_sha256": "a",
        }
        facts = [
            {
                **common,
                "id": str(year),
                "source_id": str(year),
                "period_end": f"{year}-06-30",
                "period_start": f"{year}-04-01",
                "normalized_value": value,
            }
            for year, value in [(2025, "10.02"), (2026, "23.92")]
        ]
        company = {
            "facts": facts,
            "sources": {
                "2025": {"available_from": "2025-08-01"},
                "2026": {"available_from": "2026-08-01"},
            },
        }
        self.assertIsNone(eps_comparison(company, "2026-07-31"))
        self.assertAlmostEqual(eps_comparison(company, "2026-11-05")["yoy_pct"], 138.7225548902)

    def test_flow_uses_common_window_and_never_counts_missing_as_zero(self):
        rows = [{"foreign": -1000, "investment_trust": 100, "return_pct": -1}] * 15
        rows += [{"foreign": 2000, "investment_trust": 100, "return_pct": 1}] * 5
        market = {
            "status": "available",
            "series": rows,
            "start": "2026-10-05",
            "end": "2026-11-04",
            "price_change_pct": -10,
            "trust_buy_days": 20,
            "trust_buy_down_days": 15,
        }
        result = flow_comparison(market)
        self.assertEqual((result["foreign_sell_days"], result["foreign_sell_down_days"]), (15, 15))
        self.assertEqual((result["other_days"], result["other_down_days"]), (5, 0))
        self.assertEqual(result["down_rate_gap_pp"], 100)
        self.assertEqual(result["flow_units"]["shares_per_lot"], 1000)
        rows[0] = {**rows[0], "foreign": None}
        self.assertIsNone(flow_comparison(market))

    def test_cross_review_requires_each_id_and_enforces_unavailable_effect(self):
        row = {"evidence_id": "CE-1", "effect": "unchanged", "claim": "a", "reason": "b"}
        other = {**row, "evidence_id": "CE-2", "effect": "weaken"}
        validate_cross_review([row, other], ["CE-1", "CE-2"], {})
        for bad in ([], [row, row], [row, {**other, "evidence_id": "unknown"}]):
            with self.assertRaises(ValueError):
                validate_cross_review(bad, ["CE-1", "CE-2"], {})
        with self.assertRaises(ValueError):
            validate_cross_review([row], ["CE-1"], {"kind": "unavailable"})

    def test_reading_layer_rounds_chinese_prose_and_deduplicates_summary(self):
        self.assertEqual(
            display_prose("比率33.11653886982417%，倍數1.9777629469606453倍"),
            "比率33.12%，倍數1.98倍",
        )
        self.assertEqual(
            analyst_paragraphs({"stronger_case": "摘要", "paragraphs": ["摘要 ", "細節"]}),
            ["摘要", "細節"],
        )

    def test_final_calculation_citation_requires_exact_sealed_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = put(
                root,
                "request.json",
                {
                    "research_questions": {
                        "answers": [
                            {"status": "answered", "answer": "checked observation"},
                            {"status": "unavailable", "answer": "missing"},
                        ]
                    }
                },
            )
            state = {"prompts": {"decision": {"request": request}}, "revisions": [{"inputs": {}}]}
            ref = {
                "input_id": "research_questions",
                "pointer": "/answers/0/answer",
                "quote": "checked observation",
            }
            refs(root, state, "decision", 0, [ref])
            with self.assertRaises(ValueError):
                refs(root, state, "decision", 0, [{**ref, "quote": "invented result"}])
            with self.assertRaises(ValueError):
                refs(
                    root,
                    state,
                    "decision",
                    0,
                    [{**ref, "pointer": "/answers/1/answer", "quote": "missing"}],
                )

    def test_minor_market_relation_runs_low_cost_statistic_with_small_group_flag(self):
        rows = [{"foreign": -1, "investment_trust": 1, "return_pct": -1},
                {"foreign": -1, "investment_trust": 1, "return_pct": 1}]
        rows += [{"foreign": 1, "investment_trust": 1, "return_pct": 1}] * 18
        market = {"status": "available", "series": rows, "start": "2026-10-05", "end": "2026-11-04",
                  "price_change_pct": 1, "trust_buy_days": 20, "trust_buy_down_days": 1}
        relation = {"id": "price.flow", "relationship": "alignment", "materiality": "minor",
                    "dimensions": ["technical", "institutional"]}
        with patch("tradingagents.research.research_questions.market_alignment", return_value=market):
            result = answer([relation], {}, {}, {"as_of": "2026-11-05"})
        row, = result["answers"]
        self.assertEqual(row["prior_materiality"], "minor")
        self.assertEqual(row["evidence_strength"], "limited_descriptive")
        self.assertTrue(result["flow"]["sign_sensitive"])
        self.assertEqual(len(row["evidence_ids"]), 1)

    def test_valuation_fallback_uses_only_price_and_explicit_multiple(self):
        snapshot = {"as_of": "2026-11-05", "instrument": {"stock_id": "1216"},
                    "datasets": {"TaiwanStockPrice": {"records": [
                        {"data_date": "2026-11-04", "values": {"close": 80}},
                        {"data_date": "2026-11-05", "values": {"close": 100}},
                    ]}}}
        with patch("tradingagents.research.reader_research.market_alignment", return_value={}):
            result = build(snapshot, {"financial_cases": []}, {"stock_id": "1216",
                           "as_of": "2026-11-05", "pe_axes": [20]})
        reverse = result["reverse"]
        self.assertEqual(reverse["status"], "conditional_eps_only")
        self.assertEqual(reverse["rows"], [{"assumed_pe": 20, "required_eps": 4}])

    def test_fixed_short_term_requires_price_volume_and_completed_week(self):
        raw = {"bars": [{"high": 82}] * 20 + [{"high": 85, "close": 83}],
               "dates": ["2026-11-06"], "latest": {"VOLUME_RATIO_20": 1.5, "SMA_20": 79}}
        week = {"dates": ["2026-10-30"], "bars": [{"close": 83}]}
        technical = {"status": "ready", "price_series": {"raw": raw}, "weekly": {"complete": week}}
        self.assertEqual(short_term_check(technical)["status"], "waiting_week")
        week["dates"][-1] = "2026-11-06"
        self.assertEqual(short_term_check(technical)["status"], "confirmed")
        raw["latest"]["VOLUME_RATIO_20"] = 1.49
        self.assertEqual(short_term_check(technical)["status"], "waiting_daily")
        raw["bars"][-1]["close"] = 78
        self.assertEqual(short_term_check(technical)["status"], "invalidated")

    def test_earlier_daily_breakout_can_be_confirmed_by_same_week_close(self):
        from datetime import date, timedelta

        end = date(2026, 11, 6)
        days = [(end - timedelta(days=21 - i)).isoformat() for i in range(22)]
        raw = {"bars": [{"high": 82}] * 20 + [{"high": 85, "close": 84}, {"high": 84, "close": 83}],
               "dates": days, "latest": {"VOLUME_RATIO_20": 1.2, "SMA_20": 79},
               "indicators": {"VOLUME_RATIO_20": [None] * 20 + [1.6, 1.2]}}
        week = {"dates": [days[-1]], "bars": [{"close": 83}]}
        check = short_term_check({"status": "ready", "price_series": {"raw": raw},
                                  "weekly": {"complete": week}})
        self.assertEqual(check["status"], "confirmed")
        self.assertEqual(check["candidate_date"], days[-2])
        self.assertEqual(check["breakout_level"], 82)
