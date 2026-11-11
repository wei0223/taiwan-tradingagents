import unittest
from copy import deepcopy

from tradingagents.research.market_expectations import compare_observations


def case(metric="diluted_eps"):
    value = {
        "stock_id": "1216", "metric": metric, "period_start": "2025-01-01", "period_end": "2025-12-31",
        "basis": "annual", "scope": "consolidated", "unit": "TWD" if metric == "revenue" else "TWD/share",
        "value": "12", "published_at": "2026-03-10T13:00:00+08:00", "captured_at": "2026-03-10T13:05:00+08:00",
        "available_at": "2026-03-10T13:05:00+08:00", "first_published_at": "2026-03-10T13:00:00+08:00",
        "source_kind": "official_actual", "source_sha256": "a" * 64, "version": "actual-v1",
    }
    consensus = {**value, "value": "10", "published_at": "2026-03-08T11:00:00+08:00",
                 "captured_at": "2026-03-08T11:10:00+08:00", "source_kind": "analyst_consensus",
                 "statistic": "median", "source_sha256": "b" * 64, "version": "consensus-v1"}
    return value, consensus


class ExpectationGapTests(unittest.TestCase):
    def compare(self, actual, estimates, label="eps", cutoff="2026-03-11"):
        return compare_observations("1216", cutoff, [actual], estimates)["metrics"][label]["comparisons"][0]

    def test_same_period_eps_and_revenue(self):
        for metric, label in (("diluted_eps", "eps"), ("revenue", "revenue")):
            with self.subTest(metric=metric):
                actual, consensus = case(metric)
                row = self.compare(actual, [consensus], label)
                self.assertEqual((row["status"], row["difference"], row["difference_pct"]), ("ready", "2", "20.0"))

    def test_incompatible_and_non_consensus_are_unavailable(self):
        actual, consensus = case()
        for field, value in (("basis", "single_quarter"), ("unit", None), ("metric", "basic_eps"),
                             ("scope", "standalone"), ("period_end", "2026-12-31"),
                             ("source_kind", "company_outlook"), ("source_kind", "analyst_assumption")):
            with self.subTest(field=field, value=value):
                row = self.compare(actual, [{**consensus, field: value}])
                self.assertEqual(row["status"], "unavailable")
                self.assertIsNone(row["difference"])

    def test_refuses_unproven_and_post_result_timestamps(self):
        actual, consensus = case()
        for capture in ("2026-03-08", "2026-03-10T13:00:00+08:00", "2026-04-01T13:00:00+08:00"):
            with self.subTest(capture=capture):
                row = self.compare(actual, [{**consensus, "captured_at": capture}])
                self.assertEqual(row["reason"], "no_verified_pre_release_consensus")
        revised = {**actual, "published_at": "2026-03-10T13:04:00+08:00"}
        self.assertEqual(self.compare(revised, [consensus])["reason"], "actual_initial_release_time_unverified")
        no_initial = {**actual, "first_published_at": None}
        self.assertEqual(self.compare(no_initial, [consensus])["reason"], "actual_initial_release_time_unverified")
        self.assertEqual(self.compare(actual, [consensus], cutoff="2026-03-09")["reason"], "actual_not_available_by_cutoff")

    def test_selects_last_eligible_vintage_and_flags_tied_conflict(self):
        actual, consensus = case()
        late = {**consensus, "published_at": "2026-03-10T14:00:00+08:00", "captured_at": "2026-03-10T14:10:00+08:00", "value": "12"}
        self.assertEqual(self.compare(actual, [consensus, late])["difference"], "2")
        conflict = {**deepcopy(consensus), "value": "9", "source_sha256": "c" * 64}
        self.assertEqual(self.compare(actual, [consensus, conflict])["reason"], "conflicting_latest_consensus")

    def test_zero_consensus_keeps_absolute_difference_only(self):
        actual, consensus = case()
        row = self.compare(actual, [{**consensus, "value": "0"}])
        self.assertEqual(row["difference"], "12")
        self.assertIsNone(row["difference_pct"])
