"""Publication gates must fail independently of vendor schema and prose claims."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tests.taiwan.test_report_reading import sample
from tradingagents.research.financial_model import waterfall
from tradingagents.research.forward_model import partial_case
from tradingagents.research.publishing import assess, market_gate, parameter_register
from tradingagents.research.reporting import package


def current_snapshot():
    dates = ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"]
    payload = {
        "stat": "OK",
        "date": "20261001",
        "fields": ["日期"],
        "data": [["115/10/" + d[-2:]] for d in dates],
    }
    raw = json.dumps(payload)
    archive = {
        "source_url": "https://www.twse.com.tw/exchangeReport/FMTQIK",
        "query": {"date": "20261001", "response": "json"},
        "retrieved_at": "2026-10-25T00:00:00+00:00",
        "raw_response_utf8": raw,
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
    }

    def row(day, name=None):
        return {
            "data_date": day,
            "retrieved_at": archive["retrieved_at"],
            "values": {"name": name} if name else {"close": 100},
        }

    return {
        "as_of": "2026-10-25",
        "instrument": {"exchange": "twse"},
        "market_calendar": {
            "exchange": "twse",
            "status": "ok",
            "start": dates[0],
            "end": "2026-10-25",
            "sessions": dates,
            "archives": [archive],
        },
        "datasets": {
            "TaiwanStockPrice": {"records": [row(d) for d in dates]},
            "TaiwanStockInstitutionalInvestorsBuySell": {
                "records": [
                    row(d, n)
                    for d in dates
                    for n in [
                        "Foreign_Investor",
                        "Foreign_Dealer_Self",
                        "Investment_Trust",
                        "Dealer_self",
                        "Dealer_Hedging",
                    ]
                ]
            },
        },
        "extensions": {"TaiwanStockPriceAdj": {"records": [row(d) for d in dates]}},
        "technical": {"data_date": dates[-1]},
    }


def case():
    values = {
        "revenue_twd": 1000,
        "variable_cogs_ratio": 0.4,
        "fixed_cogs_ex_depreciation_twd": 10,
        "cogs_depreciation_twd": 20,
        "variable_opex_ratio": 0.1,
        "fixed_opex_ex_depreciation_twd": 5,
        "opex_depreciation_twd": 10,
        "other_operating_twd": 0,
        "nonoperating_twd": 5,
        "tax_rate": 0.2,
        "noncontrolling_profit_twd": 10,
        "diluted_adjustment_twd": 0,
        "diluted_weighted_shares": 100,
    }
    inputs = {
        k: {
            "value": v,
            "kind": "assumption",
            "evidence_ids": ["anchor"],
            "rationale": "explicit synthetic assumption",
        }
        for k, v in values.items()
    }
    return {"period": "2026-Q4", "inputs": inputs, "calculation": waterfall(values)}


def register_fixture():
    c = case()
    meta = {
        "base": {
            c["period"]: {
                k: {
                    "source": ["doc"],
                    "observed_at": "2026-10-20T00:00:00Z",
                    "assumption_type": "inferred",
                    "validity_status": "reviewed",
                    "rationale": "synthetic driver anchor",
                    "reviewed_by": "unit-test",
                    "uncertainty": "scenario sensitivity, no probability",
                }
                for k in c["inputs"]
            }
        }
    }
    forward = {"scenarios": {"base": {"quarters": [c], "eps_sum_twd": None}}}
    docs = {"doc": {"captured_at": "2026-10-20T00:00:00Z"}}
    return c, forward, meta, docs


class PublishingTests(unittest.TestCase):
    cutoff = "2026-10-25T10:00:00+08:00"

    def test_expected_last_session_emergency_closure_weekend(self):
        s = current_snapshot()
        r = market_gate(s, self.cutoff)
        self.assertEqual(r["expected_last_session"], "2026-10-22")
        self.assertEqual(r["status"], "PASS")
        for section, name in [
            ("datasets", "TaiwanStockPrice"),
            ("extensions", "TaiwanStockPriceAdj"),
        ]:
            s[section][name]["records"] = s[section][name]["records"][:1]
        r = market_gate(s, self.cutoff)
        self.assertEqual(r["status"], "FAIL")
        self.assertEqual(r["freshness"], "STALE")
        self.assertFalse(r["current_trade_conclusion_allowed"])

    def test_old_calendar_cannot_certify_latest_market(self):
        s = current_snapshot()
        s["market_calendar"]["end"] = "2026-10-22"
        s["market_calendar"]["archives"][0]["retrieved_at"] = "2026-10-22T08:30:00Z"
        self.assertEqual(market_gate(s, self.cutoff)["status"], "FAIL")

    def test_fresh_official_month_capture_can_extend_next_day_selection_boundary(self):
        s = current_snapshot()
        s["market_calendar"]["end"] = "2026-10-24"
        self.assertEqual(market_gate(s, self.cutoff)["status"], "PASS")

    def test_missing_one_institution_group_is_not_zero(self):
        s = current_snapshot()
        rows = s["datasets"]["TaiwanStockInstitutionalInvestorsBuySell"]["records"]
        rows[:] = [
            r
            for r in rows
            if not (r["data_date"] == "2026-10-22" and r["values"]["name"] == "Investment_Trust")
        ]
        self.assertEqual(market_gate(s, self.cutoff)["status"], "FAIL")

    def test_tampered_calendar_or_future_capture_rejected(self):
        s = current_snapshot()
        s["market_calendar"]["sessions"].append("2026-10-23")
        self.assertEqual(market_gate(s, self.cutoff)["status"], "FAIL")
        s = current_snapshot()
        s["datasets"]["TaiwanStockPrice"]["records"][-1]["retrieved_at"] = "2026-10-26T00:00:00Z"
        self.assertEqual(market_gate(s, self.cutoff)["status"], "FAIL")

    def test_reviewed_inferred_parameters_allowed_without_future_actuals(self):
        _, f, m, d = register_fixture()
        r = parameter_register(f, m, d, self.cutoff)[0]
        self.assertEqual(r["formal_eps_status"], "PASS")
        self.assertEqual(r["eps"]["type"], "conditional_eps")
        self.assertIsNone(r["official_actual_eps"]["value"])

    def test_unknown_nonoperating_or_shares_block_downstream(self):
        for key in ["nonoperating_twd", "diluted_weighted_shares"]:
            c, f, m, d = register_fixture()
            c["inputs"][key].update(value=None, kind="unknown")
            checked = partial_case(c, {"anchor": 1})
            f["scenarios"]["base"]["quarters"] = [checked]
            r = parameter_register(f, m, d, self.cutoff)[0]
            self.assertEqual(r["formal_eps_status"], "BLOCKED")
            self.assertEqual(r["eps"]["type"], "unknown")
            if key == "nonoperating_twd":
                self.assertIsNone(checked["calculation"]["pretax_profit_twd"])

    def test_unqualified_zero_nonoperating_is_stress_only(self):
        c, f, m, d = register_fixture()
        c["inputs"]["nonoperating_twd"]["value"] = 0
        self.assertEqual(
            parameter_register(f, m, d, self.cutoff)[0]["presentation_tier"], "stress_only"
        )

    def test_missing_metadata_or_future_anchor_blocks_forecast(self):
        _, f, m, d = register_fixture()
        self.assertEqual(
            parameter_register(f, {}, d, self.cutoff)[0]["formal_eps_status"], "BLOCKED"
        )
        d["doc"]["captured_at"] = "2026-11-01T00:00:00Z"
        self.assertEqual(
            parameter_register(f, m, d, self.cutoff)[0]["formal_eps_status"], "BLOCKED"
        )

    def test_one_unreviewed_correction_blocks_valuation_despite_schema_pass(self):
        s = current_snapshot()
        c, f, m, docs = register_fixture()
        p = {
            "context": {"cutoff_at": self.cutoff},
            "forward": f,
            "formal_valuation_allowed": True,
            "completeness": {
                "coverage_gaps": [],
                "missing_material_events": ["unreviewed-correction"],
                "announced_earnings_periods": ["2026-Q3"],
            },
        }
        result = {
            "source_audit": {"status": "passed"},
            "generated_at": self.cutoff,
            "extension": {"research_plan": p},
            "official_source_registry": {
                "current_research": {"full_current_research_ready": True},
                "current_facts": {"source_matched_previously_reviewed_financial_numbers": True},
            },
        }
        r = assess(s, result, {"forward_parameter_metadata": m}, docs)
        self.assertEqual(r["gates"]["vendor_schema_valid"], "PASS")
        self.assertEqual(r["gates"]["official_index_covered"], "FAIL")
        self.assertEqual(r["full_valuation_status"], "BLOCKED")
        self.assertIsNone(r["valuation"]["forward_pe"])
        self.assertIsNone(r["valuation"]["fair_value"])
        self.assertIsNone(r["valuation"]["undervaluation_rank"])
        self.assertEqual(r["pending_announcements"][0]["disposition"], "pending")

    def test_waterfall_each_line_reconciles_and_sensitivity_propagates(self):
        c = case()
        v = {k: x["value"] for k, x in c["inputs"].items()}
        a = waterfall(v)
        b = waterfall({**v, "revenue_twd": 1100})
        self.assertAlmostEqual(a["gross_profit_twd"], 570)
        self.assertAlmostEqual(a["operating_profit_twd"], 455)
        self.assertAlmostEqual(a["pretax_profit_twd"], 460)
        self.assertAlmostEqual(a["parent_profit_twd"], 358)
        self.assertAlmostEqual(a["eps_twd"], 3.58)
        self.assertAlmostEqual(b["eps_twd"] - a["eps_twd"], 0.4)

    def test_package_externalizes_audit_preserves_checks_and_portability(self):
        with tempfile.TemporaryDirectory() as directory:
            r = package(copy.deepcopy(sample()), directory)
            page = (Path(directory) / "report.html").read_text(encoding="utf8")
            self.assertNotIn("content_base64", page)
            self.assertNotIn("C:/Users", page)
            self.assertNotIn("file:///", page)
            self.assertLess(len(page.encode()), 500_000)
            self.assertIn('href="publishing-quality.json"', page)
            self.assertTrue((Path(directory) / "publishing-quality.json").exists())
            self.assertEqual(r["strategy_performance"]["status"], "insufficient_sample")
            self.assertEqual(r["html_audit"]["status"], "passed")
