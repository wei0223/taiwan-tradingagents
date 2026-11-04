import base64
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from tests.taiwan.test_report_reading import notes, packet, sample
from tradingagents.research.financial_model import waterfall
from tradingagents.research.reader_research import build, market_alignment, reverse_conditions
from tradingagents.research.reporting import package


def cases():
    inputs = {
        "revenue_twd": 10000000000,
        "variable_cogs_ratio": 0.7,
        "fixed_cogs_ex_depreciation_twd": 10000000,
        "cogs_depreciation_twd": 20000000,
        "variable_opex_ratio": 0.05,
        "fixed_opex_ex_depreciation_twd": 10000000,
        "opex_depreciation_twd": 5000000,
        "other_operating_twd": 0,
        "nonoperating_twd": 10000000,
        "tax_rate": 0.2,
        "noncontrolling_profit_fraction": 0.1,
        "diluted_adjustment_twd": 0,
        "diluted_weighted_shares": 400000000,
    }
    return [
        {"period": p, "inputs": {k: {"value": v} for k, v in inputs.items()}}
        for p in ["2026-Q4", "2027-Q1", "2027-Q2", "2027-Q3"]
    ]


class ReaderResearchTests(unittest.TestCase):
    def test_inverse_conditions_reproduce_target_with_one_driver(self):
        source = cases()
        actual = reverse_conditions(source, 600, [20, 30, 40])
        base = [{k: v["value"] for k, v in c["inputs"].items()} for c in source]
        for row in actual["rows"]:
            rscale = row["required_revenue_twd"] / actual["base_revenue_twd"]
            gshift = row["gross_margin_shift_pp"] / 100
            for changed in [
                [{**v, "revenue_twd": v["revenue_twd"] * rscale} for v in base],
                [{**v, "variable_cogs_ratio": v["variable_cogs_ratio"] - gshift} for v in base],
                [
                    {
                        **v,
                        "diluted_weighted_shares": v["diluted_weighted_shares"]
                        * row["share_scale"],
                    }
                    for v in base
                ],
            ]:
                self.assertAlmostEqual(
                    sum(waterfall(v)["eps_twd"] for v in changed), row["required_eps"], places=10
                )
        self.assertFalse(actual["joint_scenarios"])

    def test_sparse_or_duplicate_quarters_not_annualized(self):
        for rows in [cases()[:1], cases()[:3], [cases()[0]] * 4]:
            self.assertNotEqual(
                reverse_conditions(rows, 600, [30])["status"], "conditional_equations_only"
            )

    def test_shocks_move_earnings_in_expected_direction_without_mutating_inputs(self):
        source = cases()
        before = copy.deepcopy(source)
        result = reverse_conditions(source, 600, [30])
        self.assertEqual(source, before)
        self.assertTrue(all(row["delta_eps"] < 0 for row in result["shocks"]))
        with self.assertRaises(ValueError):
            reverse_conditions(source, 600, [0])

    def test_missing_categories_are_not_zero(self):
        source = sample()
        day = source["datasets"]["TaiwanStockPrice"]["records"][-1]["data_date"]
        source["datasets"]["TaiwanStockInstitutionalInvestorsBuySell"]["records"] = [
            {"data_date": day, "values": {"name": name, "net": 10}}
            for name in ["Foreign_Investor", "Foreign_Dealer_Self", "Dealer"]
        ]
        result = market_alignment(source)
        self.assertIsNone(result["series"][-1]["investment_trust"])
        self.assertNotEqual(result["status"], "available")

    def test_duplicate_price_date_cannot_be_silently_compressed(self):
        source = sample()
        source["datasets"]["TaiwanStockPrice"]["records"].append(
            copy.deepcopy(source["datasets"]["TaiwanStockPrice"]["records"][-1])
        )
        with self.assertRaises(ValueError):
            market_alignment(source)

    def test_source_bytes_hash_and_cutoff_must_match(self):
        raw = b"%PDF synthetic validation fixture only"
        source = {
            "stock_id": "3017",
            "source_url": "https://example.test/fixture",
            "captured_at": "2026-10-01T00:00:00+00:00",
            "published_at": "2026-09-30",
            "pages": [1],
            "content_base64": base64.b64encode(raw).decode(),
            "sha256": "bad",
        }
        spec = {"stock_id": "3017", "as_of": "2026-10-15", "sources": {"S": source}}
        with self.assertRaises(ValueError):
            build(sample(), {"financial_cases": []}, spec)
        source["sha256"] = hashlib.sha256(raw).hexdigest()
        source["captured_at"] = "2026-10-16T00:00:00+00:00"
        with self.assertRaises(ValueError):
            build(sample(), {"financial_cases": []}, spec)

    def test_peer_periods_cannot_mix_half_year_and_quarter(self):
        raw = b"%PDF synthetic validation fixture only"
        source = {
            "stock_id": "3017",
            "source_url": "https://example.test/fixture",
            "captured_at": "2026-10-01T00:00:00+00:00",
            "published_at": "2026-09-30",
            "pages": [1],
            "content_base64": base64.b64encode(raw).decode(),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        peer = {
            "stock_id": "3017",
            "source_id": "S",
            "scope": "consolidated",
            "unit": "TWD",
            "period_basis": "ytd",
            "period_start": "2026-01-01",
            "period_end": "2026-06-30",
        }
        spec = {
            "stock_id": "3017",
            "as_of": "2026-10-15",
            "sources": {"S": source},
            "peers": [peer],
        }
        with self.assertRaises(ValueError):
            build(sample(), {"financial_cases": []}, spec)
        peer["period_basis"] = "single_quarter"
        spec["peers"].append({**peer, "period_start": "2026-04-01"})
        with self.assertRaises(ValueError):
            build(sample(), {"financial_cases": []}, spec)

    def test_reader_metadata_must_match_source(self):
        with self.assertRaises(ValueError):
            build(sample(), {"financial_cases": []}, {"stock_id": "3231", "as_of": "2026-10-15"})

    def test_reader_has_conclusion_first_and_no_empty_industry_or_system_status(self):
        with tempfile.TemporaryDirectory() as folder:
            package(
                sample(),
                folder,
                analyst_prose=notes(),
                review_input=packet(),
                extension_input={"stock_id": "3017", "as_of": "2026-10-15", "one_shot": True},
            )
            text = (Path(folder) / "report.html").read_text(encoding="utf-8")
            audit = (Path(folder) / "audit.html").read_text(encoding="utf-8")
            self.assertLess(text.index('id="conclusion"'), text.index('id="industry"'))
            self.assertNotIn("產業與競爭力證據", text)
            self.assertNotIn("PENDING", text)
            self.assertNotIn("BLOCKED", text)
            self.assertIn("audit.html", text)
            self.assertIn("產業與競爭力證據", audit)
            self.assertNotIn("file:///", text)


if __name__ == "__main__":
    unittest.main()
