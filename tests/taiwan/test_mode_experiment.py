import copy
import unittest

from tradingagents.research.bundle import fingerprint
from tradingagents.research.mode_experiment import MODES, RUBRIC, compare


def fixture():
    cell = {"stock_id": "1216", "as_of": "2026-11-07", "split": "holdout", "input_sha256": "same"}
    key = fingerprint(cell)
    plan = {"cells": {key: cell}, "model_control": "fixed-model", "randomness_control": "fixed-seed",
            "frozen_at": "2026-11-10T08:00:00+08:00", "rubric_sha256": fingerprint(RUBRIC)}
    plan["plan_sha256"] = fingerprint(plan)
    output = {"stock_id": "1216", "as_of": "2026-11-07", "stance": "neutral", "summary": "Fixture",
              "claims": [], "counterevidence": [], "limits": ["fixture"], "invalidation": []}
    output_sha = fingerprint(output)
    runs = []
    for i, mode in enumerate(MODES):
        run = {"cell_id": key, "mode": mode, "plan_sha256": plan["plan_sha256"], "input_sha256": "same",
               "started_at": "2026-11-10T09:00:00+08:00", "finished_at": "2026-11-10T09:01:00+08:00",
               "model_control": "fixed-model", "randomness_control": "fixed-seed", "status": "completed",
               "dispatches": i + 1, "retries": None, "output": copy.deepcopy(output), "output_sha256": output_sha,
               "review": {"output_sha256": output_sha, "rubric_sha256": plan["rubric_sha256"],
                          "reviewer": "blind reviewer", "blind": True, "ratings": dict.fromkeys(RUBRIC, 2),
                          "inference_errors": 0, "material_omissions": 0}}
        run["run_sha256"] = fingerprint(run)
        runs.append(run)
    return plan, runs


def rehash(run):
    run.pop("run_sha256", None)
    run["run_sha256"] = fingerprint(run)


class ModeExperimentTests(unittest.TestCase):
    def test_only_common_blind_holdout_has_means(self):
        plan, runs = fixture()
        result = compare(plan, runs)
        self.assertEqual(len(result["paired_holdout_cells"]), 1)
        self.assertEqual(result["paired_holdout_means"]["single_ai"]["score"], 8)
        self.assertIsNone(result["paired_holdout_means"]["single_ai"]["retries"])
        self.assertFalse(result["research_benefit_proven"])

    def test_missing_and_uncontrolled_modes_are_not_paired(self):
        plan, runs = fixture()
        self.assertEqual(compare(plan, runs[:-1])["paired_holdout_cells"], [])
        runs[-1]["model_control"] = None
        rehash(runs[-1])
        self.assertEqual(compare(plan, runs)["paired_holdout_cells"], [])
        self.assertIsNone(compare(plan, [])["mode_totals"]["single_ai"]["success_rate"])

    def test_altered_input_or_output_cannot_enter_pair(self):
        plan, runs = fixture()
        run = runs[0]
        run["input_sha256"] = "different"
        rehash(run)
        with self.assertRaisesRegex(ValueError, "frozen comparison cell"):
            compare(plan, runs)
        run["input_sha256"] = "same"
        run["output"]["summary"] = "changed"
        rehash(run)
        with self.assertRaisesRegex(ValueError, "output must match"):
            compare(plan, runs)

    def test_prior_or_unblinded_execution_is_rejected(self):
        plan, runs = fixture()
        runs[0]["started_at"] = "2026-11-10T07:00:00+08:00"
        rehash(runs[0])
        with self.assertRaisesRegex(ValueError, "after experiment freeze"):
            compare(plan, runs)
        runs[0]["started_at"] = "2026-11-10T09:00:00+08:00"
        runs[0]["review"]["blind"] = False
        rehash(runs[0])
        with self.assertRaisesRegex(ValueError, "Blind review"):
            compare(plan, runs)
