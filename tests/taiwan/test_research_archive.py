import json
import tempfile
import unittest
from pathlib import Path

from tradingagents.research.forecast_journal import read_research, seal_research


class ResearchArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def edition(self, name, clock, as_of="2026-11-07", mode="research"):
        directory = self.root / name
        state = {"mode": mode, "stock_id": "1216", "as_of": as_of, "active_revision": 0,
                 "snapshot_sha256": "snapshot", "templates": {"common.md": "prompt-version"},
                 "protocol": "taiwan-eight-stage-v2"}
        opinion = {"agent_id": "agent", "stance": "neutral", "summary": "Fixture",
                   "conditions": ["Recheck"], "prompt_sha256": "prompt", "findings": []}
        members = {"agent-run/state.json": state, "research.json": {}, "snapshot.json": {}, "report.html": {}}
        members.update({f"agent-run/jobs/{job}/accepted.json": opinion
                        for job in ("fundamental", "technical", "institutional", "decision")})
        for member, value in members.items():
            path = directory / member
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding="utf8")
        data = {"stock_id": "1216", "as_of": as_of, "generated_at": clock,
                "decision_conditions": {"status": "conditional_candidate"},
                "valuation_scenarios": {"status": "limited", "scenarios": {}, "periods": []},
                "horizon_analysis": {}}
        return directory, data

    def test_current_candidate_is_pending_and_archive_is_read_only(self):
        directory, data = self.edition("today", "2026-11-07T10:00:00+00:00")
        record = seal_research(directory, data)
        self.assertEqual(record["scoring_status"], "pending")
        self.assertEqual(read_research(directory)[0], record)
        with self.assertRaisesRegex(ValueError, "Never overwrite"):
            seal_research(directory, data)
        (directory / "snapshot.json").write_text("tampered", encoding="utf8")
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            read_research(directory)

    def test_correction_gets_new_identity_and_keeps_original(self):
        first, data = self.edition("first", "2026-11-07T10:00:00+00:00")
        original = seal_research(first, data)
        second, new_data = self.edition("second", "2026-11-10T10:00:00+00:00")
        corrected = seal_research(second, new_data, first)
        self.assertNotEqual(corrected["research_id"], original["research_id"])
        self.assertEqual(corrected["version"], 2)
        self.assertEqual(corrected["supersedes"]["research_id"], original["research_id"])
        self.assertEqual(corrected["scoring_status"], "ineligible_retrospective")
        self.assertEqual(read_research(first)[0], original)
        self.assertFalse(corrected["formal_prediction_release_issued"])

    def test_mock_cannot_create_a_research_archive(self):
        directory, data = self.edition("mock", "2026-11-07T10:00:00+00:00", mode="mock")
        with self.assertRaisesRegex(ValueError, "non-mock"):
            seal_research(directory, data)
        self.assertFalse((directory / "forecast-record.json").exists())
