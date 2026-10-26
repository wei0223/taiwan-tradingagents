"""Synthetic control tests; these are never financial performance evidence."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tests.taiwan.test_forward_estimate import fixture
from tests.taiwan.test_originals import original, row
from tradingagents.research import output_registry, pit_validation, resolution
from tradingagents.research.bundle import fingerprint
from tradingagents.research.forward_estimate import build
from tradingagents.research.v6_review import debate_trigger, parameters


def estimate():
    s, p, q, d = fixture()
    return build(s, p, q, d, "3017")


def work(events=None):
    event = {"source_id": "3017|115/10/10|15:00:00|F22|"+row()["HYPERLINK"],
             "categories": ["monthly_revenue"], "published_at": "2026-10-10T15:00:00+08:00",
             "subject": row()["SUBJECT"], "reason": "unreviewed", "status": "MISSING_PUBLISHED"}
    return resolution.queue("3017", events or [event], {"context": {"cutoff_at": "2026-10-25T00:00:00Z"}})


def vintage(year, month, capture=None):
    raw = {"stock_id": "3017", "revenue_year": year, "revenue_month": month,
           "revenue": 1000+(year-2015)*100+month}
    published = f"{year+(month==12):04d}-{month%12+1:02d}-10T00:00:00Z"
    return {"stock_id": "3017", "period": f"{year:04d}-{month:02d}", "value": raw["revenue"],
            "raw": raw, "source_sha256": fingerprint(raw), "revision_id": "initial-observation",
            "published_at": published, "captured_at": capture or published}


def pit_fixture(backfill=False):
    rows = [vintage(y, m, "2026-10-25T00:00:00Z" if backfill else None)
            for y in range(2015, 2022) for m in range(1, 13)]
    folds = [{"name": str(y), "train_end": f"{y-1}-12-31T23:59:59Z",
              "test_start": f"{y}-01-01T00:00:00Z", "test_end": f"{y}-12-31T23:59:59Z",
              "origins": [{"as_of": f"{y}-06-30T23:59:59Z", "target": f"{y}-07"}]}
             for y in (2019, 2021)]
    plan = {"stock_id": "3017", "model_version": "synthetic-fixed-v6",
            "registered_at": "2026-10-25T00:00:00Z", "folds": folds}
    plan["plan_sha256"] = fingerprint(plan)
    return rows, folds, plan


class RegistryTests(unittest.TestCase):
    def registry(self, model=None):
        return output_registry.build({"as_of": "2026-10-25"}, model or estimate(), "2026-10-26T00:00:00Z")

    def test_all_outputs_bound_and_reproducible(self):
        model = estimate()
        r = self.registry(model)
        self.assertTrue(output_registry.validate(r))
        self.assertEqual(output_registry.materialize(model, r), model)
        self.assertTrue(all(x["model_version"] == r["model_version"] for x in r["rows"]))

    def test_modified_output_rejected(self):
        r = self.registry()
        r["rows"][0]["value"] += 1
        with self.assertRaises(ValueError):
            output_registry.validate(r)

    def test_duplicate_state_rejected(self):
        r = self.registry()
        r["rows"].append(copy.deepcopy(r["rows"][0]))
        with self.assertRaises(ValueError):
            output_registry.validate(r)

    def test_different_model_rejected(self):
        model = estimate()
        r = self.registry(model)
        model["methodology"] = "changed"
        with self.assertRaises(ValueError):
            output_registry.materialize(model, r)

    def test_unqualified_old_contradiction_rejected(self):
        with self.assertRaises(ValueError):
            output_registry.audit_presentation("<p>歸母橋不足，商數不可用</p>", self.registry())

    def test_explicit_historical_scope_allowed(self):
        self.assertEqual(output_registry.audit_presentation(
            '<section data-scope="historical">歸母橋不足，商數不可用</section>', self.registry())["status"], "passed")

    def test_empty_pending_rejected(self):
        with self.assertRaises(ValueError):
            output_registry.audit_presentation("<p>待補：</p>", self.registry())

    def test_truncated_matrix_rejected(self):
        model = estimate()
        model["scenarios"]["base"]["price_sensitivity_twd"].pop()
        with self.assertRaises(ValueError):
            self.registry(model)

    def test_blocked_model_no_current_eps(self):
        s, p, q, d = fixture()
        q["forward_parameter_register"][0]["formal_eps_status"] = "BLOCKED"
        r = self.registry(build(s, p, q, d, "3017"))
        self.assertFalse(any(x["qualification"] == "conditional" and x["metric"] == "eps_twd"
                             and x["status"] == "AVAILABLE" for x in r["rows"]))


class ResolutionTests(unittest.TestCase):
    def test_required_fields_and_gate(self):
        w = work()
        keys = {"event_id", "company", "type", "published_at", "source_url", "revision_of",
                "observed_at", "impact_tag", "severity", "reason", "resolution_action", "resolution_state"}
        self.assertTrue(keys <= w["items"][0].keys())
        self.assertEqual(resolution.gates(w, {"status": "PASS"}, "2026-10-25")["formal_valuation_inputs"], "BLOCKED")

    def test_deduplicates_only_complete_identity(self):
        event = {"source_id": "x", "categories": [], "status": "MISSING_PUBLISHED", "reason": "gap"}
        w = work([event, {**event, "source_id": "y"}])
        self.assertEqual(len(w["items"]), 2)
        e = work()["items"][0]
        event.update(source_id=e["aliases"][0], published_at=e["published_at"], subject=e["subject"])
        self.assertEqual(len(work([event, event])["items"]), 1)

    def test_historical_gap_blocks_backtest_not_current_denominator(self):
        w = work([{"source_id": "history:7", "historical_backtest_block": True,
                   "categories": [], "status": "REVISION_UNRESOLVED", "reason": "no original event"}])
        self.assertEqual(w["counts"]["historical_gap_groups"], 1)
        self.assertFalse(w["items"][0]["affects_current_denominator"])

    def test_model_cannot_waive_own_p0(self):
        w = work()
        waiver = {"event_id": w["items"][0]["event_id"], "approved_by": "model", "reason": "fine",
                  "approved_at": "2026-10-24", "expires_at": "2026-10-27"}
        self.assertEqual(resolution.gates(w, {}, "2026-10-25", [waiver])["formal_valuation_inputs"], "BLOCKED")

    def test_cached_original_still_requires_numeric_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = resolution.execute(work(), [row()], {"d": original()}, tmp, now="2026-10-25T00:00:00Z")
        self.assertEqual(r["items"][0]["resolution_state"], "ACQUIRED_PENDING_REVIEW")
        self.assertFalse(r["items"][0]["numeric_impact_verified"])
        self.assertFalse(r["P0_warnings_removed"])

    def test_zero_budget_never_calls_client(self):
        class Client:
            def capture(self, event):
                raise AssertionError("No request authorized by budget")
        with tempfile.TemporaryDirectory() as tmp:
            r = resolution.execute(work(), [row()], {}, tmp, Client(), "2026-10-25", 0)
        self.assertIn("budget_deferred", r["items"][0]["reason"])

    def test_refusal_saved_and_next_attempt_cooled_down(self):
        class Client:
            calls = 0
            def capture(self, event):
                self.calls += 1
                return {"http_status": 403, "status": "source_rejected", "source_url": event["HYPERLINK"]}
        client = Client()
        with tempfile.TemporaryDirectory() as tmp:
            resolution.execute(work(), [row()], {}, tmp, client, "2026-10-25T00:00:00Z", 1)
            r = resolution.execute(work(), [row()], {}, tmp, client, "2026-10-25T01:00:00Z", 1)
            self.assertEqual(len(list((Path(tmp)/"captured").glob("*.json"))), 1)
        self.assertEqual(client.calls, 1)
        self.assertIn("cooldown", r["items"][0]["reason"])

    def test_attempt_history_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            resolution.execute(work(), [], {}, tmp, now="2026-10-25", budget=0)
            path = next(Path(tmp).glob("*.json"))
            data = json.loads(path.read_text())
            data["reason"] = "changed"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                resolution.execute(work(), [], {}, tmp, now="2026-10-26", budget=0)

    def test_budget_deferral_does_not_create_network_cooldown(self):
        class Client:
            calls = 0
            def capture(self, event):
                self.calls += 1
                return {"status": "unavailable", "http_status": 500}
        client = Client()
        with tempfile.TemporaryDirectory() as tmp:
            resolution.execute(work(), [row()], {}, tmp, now="2026-10-25", budget=0)
            resolution.execute(work(), [row()], {}, tmp, client, "2026-10-25T01:00:00Z", 1)
        self.assertEqual(client.calls, 1)


class PITTests(unittest.TestCase):
    def test_two_synthetic_isolated_folds_no_prospective_claim(self):
        rows, folds, plan = pit_fixture()
        result = pit_validation.replay(rows, [], folds, plan, "2026-10-26")
        self.assertEqual(result["completed_input_isolated_folds"], 2)
        self.assertFalse(result["strict_prospective_performance"])
        self.assertTrue(all(not f["plan_predates_test"] for f in result["folds"]))

    def test_current_backfills_cannot_become_historical_inputs(self):
        rows, folds, plan = pit_fixture(True)
        result = pit_validation.replay(rows, [], folds, plan, "2026-10-26")
        self.assertEqual(result["completed_input_isolated_folds"], 0)
        self.assertTrue(all(f["status"] == "INPUTS_UNAVAILABLE" for f in result["folds"]))

    def test_future_inputs_do_not_tune_weights(self):
        rows, folds, plan = pit_fixture()
        before = pit_validation.replay(rows, [], folds, plan, "2026-10-26")
        extra = vintage(2022, 1)
        after = pit_validation.replay(rows+[extra], [], folds, plan, "2026-10-26")
        self.assertEqual([f["weights"] for f in before["folds"]], [f["weights"] for f in after["folds"]])

    def test_frozen_plan_tamper_rejected(self):
        rows, folds, plan = pit_fixture()
        plan["model_version"] = "changed"
        with self.assertRaises(ValueError):
            pit_validation.replay(rows, [], folds, plan, "2026-10-26")

    def test_mixed_company_rejected(self):
        rows, folds, plan = pit_fixture()
        rows[0]["stock_id"] = "2330"
        with self.assertRaises(ValueError):
            pit_validation.replay(rows, [], folds, plan, "2026-10-26")

    def test_raw_value_tamper_rejected(self):
        r = vintage(2020, 1)
        r["value"] += 1
        with self.assertRaises(ValueError):
            pit_validation.known([r], "2026-10-26")

    def test_ambiguous_vintage_rejected(self):
        r = vintage(2020, 1)
        with self.assertRaises(ValueError):
            pit_validation.known([r, {**r, "revision_id": "other"}], "2026-10-26")

    def test_unknown_publication_never_invented_or_backdated(self):
        r = vintage(2020, 1, "2026-10-25T00:00:00Z")
        r.update(published_at=None, public_capture_verified=True, publication_time_status="unknown_current_revision")
        self.assertEqual(pit_validation.known([r], "2021-01-01"), {})
        self.assertEqual(len(pit_validation.known([r], "2026-10-26")), 1)

    def test_unknown_publication_without_capture_proof_rejected(self):
        r = vintage(2020, 1)
        r["published_at"] = None
        with self.assertRaises(ValueError):
            pit_validation.known([r], "2026-10-26")

    def test_metrics_zero_actual_is_not_dropped_from_mae(self):
        r = pit_validation.metrics([{"prediction": 2, "actual": 0}])
        self.assertEqual(r["n"], 1)
        self.assertEqual(r["mae"], 2)
        self.assertEqual(r["mape_n"], 0)
        self.assertIsNone(r["forecast_hit_rate"])

    def test_already_public_not_prediction_accuracy(self):
        self.assertEqual(pit_validation.attribution({}, {}, "2026-10-25", "2026-10-24")["status"], "ineligible_already_public")

    def test_numerator_denominator_bridge_exact(self):
        r = pit_validation.attribution({"diluted_numerator_twd": 120, "diluted_weighted_shares": 12, "eps_twd": 10},
            {"diluted_numerator_twd": 100, "diluted_weighted_shares": 10, "eps_twd": 10}, "2026-10-20", "2026-10-25")
        b = r["eps_bridge"]
        self.assertAlmostEqual(b["numerator_effect_twd_per_share"]+b["denominator_effect_twd_per_share"], b["exact_quotient_error"])


class ClassificationTests(unittest.TestCase):
    def parameter(self):
        return {"scenario": "base", "period": "2026-Q4", "parameter_name": "gross_margin", "value": .2,
                "validity_status": "reviewed", "formula": "explicit assumption", "source_availability": [],
                "alternatives": [], "sensitivity": [], "historical_error": None}

    def test_schema_review_is_not_calibration(self):
        result = parameters([self.parameter()], {}, "2026-10-25")[0]
        self.assertEqual(result["validation"]["schema_valid"], "PASS")
        self.assertEqual(result["validation"]["historical_calibrated"], "FAIL")
        self.assertEqual(result["validation"]["forward_validated"], "FAIL")
        self.assertIsNone(result["base_probability"])

    def test_future_actual_classification_rejected(self):
        spec = {"forward_parameter_metadata": {"base": {"2026-Q4": {"gross_margin": {"forecast_kind": "observed"}}}}}
        with self.assertRaises(ValueError):
            parameters([self.parameter()], spec, "2026-10-25")

    def test_no_new_numbers_no_debate(self):
        self.assertFalse(debate_trigger([], [], {}, "2026-10-25")["should_trigger"])

    def test_self_review_flag_without_original_does_not_trigger(self):
        before = [{"stock_id": "3017", "period": "2026-Q2", "metric": "eps_twd", "value": 1}]
        after = [{**before[0], "value": 2, "reviewed_by": "model", "source_id": "absent"}]
        self.assertFalse(debate_trigger(before, after, {}, "2026-10-25")["should_trigger"])

    def test_reviewed_new_material_change_triggers_once(self):
        _, _, _, docs = fixture()
        docs["doc"]["revision_id"] = "synthetic-2"
        before = [{"stock_id": "3017", "period": "2026-Q2", "metric": "eps_twd", "value": 1,
                   "source_sha256": "old-version"}]
        after = [{**before[0], "value": 2, "reviewed_by": "synthetic reviewer", "source_id": "doc",
                  "source_sha256": docs["doc"]["sha256"], "source_locator": "synthetic EPS column",
                  "values_reviewed": True}]
        r = debate_trigger(before, after, docs, "2026-10-25")
        self.assertTrue(r["should_trigger"])
        self.assertFalse(debate_trigger(before, after, docs, "2026-10-25", [r["change_id"]])["should_trigger"])
