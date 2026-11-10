import json
import tempfile
import unittest
from pathlib import Path

from tests.taiwan.test_eight_stage import fixture
from tradingagents.research.eight_stage import export
from tradingagents.research.workflow_optimization import _elapsed, compare_costs


class WorkflowOptimizationTests(unittest.TestCase):
    def test_failed_export_leaves_no_published_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, *_ = fixture(tmp)
            output = Path(tmp) / "portable"
            with self.assertRaisesRegex(ValueError, "Every eight-stage job"):
                export(source, output)
            self.assertFalse(output.exists())
            self.assertTrue((source / "state.json").exists())

    def test_missing_timing_and_missing_holdout_stay_unknown(self):
        self.assertIsNone(_elapsed({"started_at": "2026-11-07"}))
        self.assertEqual(_elapsed({"started_at": "2026-11-07T08:00:00+08:00",
                                   "finished_at": "2026-11-07T08:00:12+08:00"}), 12)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "comparison.json"
            source.write_text(json.dumps({"plan_sha256": "fixed", "paired_holdout_cells": [],
                                          "paired_holdout_means": {}}), encoding="utf8")
            result = compare_costs(source, Path(tmp) / "cost")
            self.assertEqual(result["policy"], "retain_complete_eight_stage")
            self.assertIsNone(result["paired_holdout_means"])
            self.assertIsNone(result["api_cost"])
            self.assertFalse(result["quality_noninferiority_proven"])
