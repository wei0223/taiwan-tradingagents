"""Regression coverage for calculated research evidence and presentation."""

import tempfile
import unittest
from pathlib import Path

from tradingagents.research.eight_stage import put, refs, validate_cross_review
from tradingagents.research.research_questions import (
    analyst_paragraphs,
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
