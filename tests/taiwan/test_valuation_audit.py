import unittest
from copy import deepcopy

from tradingagents.research.valuation_audit import (
    eps_inventory,
    peer_basis,
    quarter_window,
    single_quarter_reconciliation,
    vendor_history,
)

CUTOFF = "2026-10-25T23:59:59+00:00"


def evidence(period):
    return {"period": period, "stock_id": "3017", "currency": "TWD", "scope": "consolidated",
            "basis": "direct_reported_single_quarter_diluted_eps", "source_sha256": "a" * 64,
            "source_url": "https://example.org/report", "locator": "quarter column",
            "reviewed_by": "original reviewer", "captured_at": CUTOFF,
            "original_bytes_verified": True, "eps_twd": 2.5}


class ValuationAuditTests(unittest.TestCase):
    def test_mixed_ytd_amounts_and_quarterly_eps_cannot_pass_as_single_quarter(self):
        vendor = {"Revenue": 1741745642000, "OperatingIncome": 62584549000,
                  "EquityAttributableToOwnersOfParent": 24458222000, "EPS": 4.72}
        official = {"revenue_twd": 895442616000, "operating_profit_twd": 33447284000,
                    "parent_profit_twd": 14827483000}
        result = single_quarter_reconciliation(vendor, official)
        assert result["status"] == "not_eligible_as_verified_single_quarter"
        assert not result["eps_differenced"]

    def test_window_uses_actual_earnings_and_rolls_year(self):
        assert quarter_window("2026-Q2") == ["2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2"]


    def test_annual_eps_cannot_complete_missing_q4(self):
        rows = [evidence(p) for p in quarter_window("2026-Q2")]
        rows[1]["basis"] = "annual_diluted"
        result = eps_inventory("3017", "2026-Q2", rows, CUTOFF)
        assert result["verified_count"] == 3
        assert result["ttm_reported_quarter_sum_twd"] is None


    def test_inventory_excludes_bad_or_future_evidence(self):
        for key, value in [("stock_id", "3231"), ("currency", "USD"),
                           ("scope", "parent_only"), ("original_bytes_verified", False),
                           ("eps_twd", float("nan")),
                           ("captured_at", "2026-10-26T00:00:00+00:00"),
                           ("published_at", "2026-10-26T00:00:00+00:00")]:
            with self.subTest(field=key):
                rows = [evidence(p) for p in quarter_window("2026-Q2")]
                rows[0][key] = value
                assert eps_inventory("3017", "2026-Q2", rows, CUTOFF)["verified_count"] == 3


    def test_duplicate_revision_requires_explicit_selection(self):
        row = evidence("2025-Q3")
        with self.assertRaisesRegex(ValueError, "revision"):
            eps_inventory("3017", "2026-Q2", [row, deepcopy(row)], CUTOFF)


    def test_complete_sum_is_not_statutory_weighted_ttm_or_pit(self):
        result = eps_inventory("3017", "2026-Q2",
                               [evidence(p) for p in quarter_window("2026-Q2")], CUTOFF)
        assert result["ttm_reported_quarter_sum_twd"] == 10
        assert result["statutory_twelve_month_weighted_eps"] is None
        assert result["strict_historical_pit_verified"] is False


    def test_vendor_sample_excludes_future_negative_and_never_certifies_forward_pe(self):
        rows = [{"data_date": day, "retrieved_at": CUTOFF, "raw_sha256": "a" * 64,
                 "values": {"reported_pe": pe}}
                for day, pe in [("2026-10-20", 10), ("2026-10-21", -5), ("2026-10-26", 30)]]
        result = vendor_history(rows, CUTOFF)
        assert result["count"] == 1 and len(result["excluded"]) == 2
        assert result["usable_as_forward_fair_multiple"] is False


    def test_same_day_vendor_pe_does_not_become_comparable_forward_pe(self):
        row = {"price_date": "2026-10-19", "currency": "TWD", "scope": "consolidated",
               "eps_basis": "vendor_reported", "denominator_verified": False}
        assert peer_basis(row, row)["status"] == "not_eligible_for_forward_multiple"


    def test_mismatched_forecast_horizon_fails_even_if_diluted(self):
        row = {"price_date": "2026-10-19", "currency": "TWD", "scope": "consolidated",
               "eps_basis": "forward_four_quarter_diluted", "denominator_verified": True,
               "source_sha256": "a" * 64, "forecast_start": "2026-Q4", "forecast_end": "2027-Q3"}
        peer = {**row, "forecast_end": "2027-Q4"}
        assert "missing_or_mismatched_forecast_end" in peer_basis(row, peer)["reasons"]
