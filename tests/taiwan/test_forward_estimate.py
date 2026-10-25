"""Future assumptions may calculate without pretending they are future actuals."""

import base64
import copy
import hashlib
import unittest

from tests.taiwan.test_publishing import register_fixture
from tradingagents.research.forward_estimate import build, ttm
from tradingagents.research.publishing import parameter_register


def fixture():
    c, _, m, _ = register_fixture()
    raw = b"Synthetic original, not issuer data"
    docs = {
        "doc": {
            "stock_id": "3017",
            "source_url": "https://example.org/official",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "content_base64": base64.b64encode(raw).decode(),
            "published_at": "2026-10-20T00:00:00Z",
            "captured_at": "2026-10-20T00:00:00Z",
            "public_capture_verified": True,
        }
    }
    targets = ["2026-Q4", "2027-Q1", "2027-Q2", "2027-Q3"]
    scenarios = {}
    metadata = {}
    for name in ["bear", "base", "bull"]:
        rows = []
        metadata[name] = {}
        for q in targets:
            row = copy.deepcopy(c)
            row["period"] = q
            rows.append(row)
            metadata[name][q] = copy.deepcopy(m["base"]["2026-Q4"])
        scenarios[name] = {
            "quarters": rows,
            "eps_sum_twd": sum(r["calculation"]["eps_twd"] for r in rows),
        }
    forward = {"target_quarters": targets, "scenarios": scenarios}
    ctx = {
        "cutoff_at": "2026-10-25T12:00:00Z",
        "mode": "retrospective_diagnostic",
        "stock_id": "3017",
    }
    quality = {
        "forward_parameter_register": parameter_register(forward, metadata, docs, ctx["cutoff_at"]),
        "valuation": {"price_twd": 100, "price_date": "2026-10-22"},
        "latest_market_gate": {"status": "PASS"},
    }
    spec = {
        "stock_id": "3017",
        "currency": "TWD",
        "methodology": "Synthetic explicit assumptions",
        "reviewed_by": "unit-test",
        "pe_sensitivity": [10, 20],
        "pe_rationale": "synthetic sensitivity only",
        "ttm_quarters": [],
    }
    return spec, {"context": ctx, "forward": forward}, quality, docs


class ForwardEstimateTests(unittest.TestCase):
    def test_inferred_future_shares_allow_conditional_pe_not_fair_value(self):
        s, p, q, d = fixture()
        r = build(s, p, q, d, "3017")
        self.assertEqual(r["status"], "conditional_estimate_reviewed_assumptions")
        self.assertAlmostEqual(r["scenarios"]["base"]["four_quarter_eps_sum_twd"], 14.32)
        self.assertAlmostEqual(r["scenarios"]["base"]["forward_pe"], 100 / 14.32)
        self.assertEqual(r["scenarios"]["base"]["price_sensitivity_twd"], [143.2, 286.4])
        self.assertIsNone(r["full_fair_value"])
        self.assertIsNone(r["undervaluation_rank"])
        self.assertEqual(len(r["sensitivity"]), 5)

    def test_stale_market_keeps_eps_but_blocks_price_outputs(self):
        s, p, q, d = fixture()
        q["latest_market_gate"]["status"] = "FAIL"
        r = build(s, p, q, d, "3017")
        self.assertIsNotNone(r["scenarios"]["base"]["four_quarter_eps_sum_twd"])
        self.assertIsNone(r["scenarios"]["base"]["forward_pe"])
        self.assertEqual(r["reverse"], [])

    def test_missing_parameter_metadata_blocks_eps_pe_and_matrix(self):
        s, p, q, d = fixture()
        q["forward_parameter_register"][0]["formal_eps_status"] = "BLOCKED"
        r = build(s, p, q, d, "3017")
        self.assertEqual(r["status"], "BLOCKED")
        self.assertIsNone(r["scenarios"]["base"]["four_quarter_eps_sum_twd"])
        self.assertEqual(r["scenarios"]["base"]["price_sensitivity_twd"], [])

    def test_serialized_calculation_tampering_rejected(self):
        s, p, q, d = fixture()
        p["forward"]["scenarios"]["base"]["quarters"][0]["calculation"]["eps_twd"] += 1
        with self.assertRaises(ValueError):
            build(s, p, q, d, "3017")

    def test_wrong_company_or_source_hash_rejected(self):
        s, p, q, d = fixture()
        with self.assertRaises(ValueError):
            build(s, p, q, d, "2330")
        d["doc"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            build(s, p, q, d, "3017")

    def test_aggregate_eps_tampering_rejected(self):
        s, p, q, d = fixture()
        p["forward"]["scenarios"]["base"]["eps_sum_twd"] += 1
        with self.assertRaises(ValueError):
            build(s, p, q, d, "3017")

    def test_ttm_only_direct_comparable_quarterly_diluted_disclosures(self):
        _, p, _, d = fixture()
        rows = [
            {
                "stock_id": "3017",
                "period": period,
                "eps_twd": i + 1,
                "source_id": "doc",
                "currency": "TWD",
                "basis": "direct_reported_single_quarter_diluted_eps",
                "reviewed_by": "unit-test",
                "locator": "synthetic",
                "initial_or_revised_vintage": "captured_current",
            }
            for i, period in enumerate(["2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2"])
        ]
        r = ttm(rows, d, p["context"], "3017")
        self.assertEqual(r["eps_twd"], 10)
        rows[1]["basis"] = "annual_minus_nine_month_eps"
        with self.assertRaises(ValueError):
            ttm(rows, d, p["context"], "3017")
        self.assertIsNone(ttm([], d, p["context"], "3017")["eps_twd"])

    def test_ttm_basic_or_duplicate_quarter_rejected(self):
        _, p, _, d = fixture()
        row = {
            "stock_id": "3017",
            "period": "2025-Q4",
            "eps_twd": 1,
            "source_id": "doc",
            "currency": "TWD",
            "basis": "direct_reported_single_quarter_diluted_eps",
            "reviewed_by": "unit-test",
            "locator": "synthetic",
            "initial_or_revised_vintage": "captured_current",
        }
        with self.assertRaises(ValueError):
            ttm([row] * 4, d, p["context"], "3017")
