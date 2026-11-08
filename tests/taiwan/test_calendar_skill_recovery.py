"""Daily helper resumes a new edition and binds the effective source, offline."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "skills/taiwan-report/scripts/prepare_research.py"
spec = importlib.util.spec_from_file_location("calendar_skill", SCRIPT)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class CalendarSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {"repository": str(REPO), "calendar_cache_dir": str(self.root / "cache")}
        self.write("project.json", self.config)
        self.write("request.json", {"upgrade_extension_defaults": {"agent_protocol": "taiwan-eight-stage-v2"}})
        self.write("snapshot.json", {"synthetic": "original"})
        self.source = helper.read(self.root / "snapshot.json")
        self.write("preparation.json", {"status": "data_available", "snapshot": "snapshot.json",
                                      "request_sha256": self.sha("request.json"), "snapshot_sha256": self.sha("snapshot.json")})

    def sha(self, name):
        return hashlib.sha256((self.root / name).read_bytes()).hexdigest()

    def write(self, name, value):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value), encoding="utf8")

    def prepared(self, output, status, effective=None):
        technical = {"implementation_sha256": hashlib.sha256((REPO / "tradingagents/research/technical_engine.py").read_bytes()).hexdigest()}
        company = {"synthetic": "company"}
        state = {"agent_protocol": "taiwan-eight-stage-v2", "technical_status": status,
                 "technical_sha256": helper.digest(technical), "company_plan_sha256": helper.digest(company),
                 "source_snapshot_sha256": helper.digest(effective or self.source)}
        if effective:
            state.update(acquisition_snapshot_sha256=helper.digest(self.source), effective_snapshot="effective-snapshot.json")
            self.write(output / "effective-snapshot.json", effective)
        for name, value in [("technical-package.json", technical), ("company-plan.json", company), ("fixed-state.json", state)]:
            self.write(output / name, value)

    def invoke(self):
        with patch.object(helper, "__file__", str(self.root / "prepare_research.py")), patch("sys.argv", ["prepare_research.py", "--request", str(self.root / "request.json")]), redirect_stdout(StringIO()) as out:
            code = helper.main()
        return code, json.loads(out.getvalue())

    def test_blocked_edition_recovers_once_without_overwriting_and_reuses_pointer(self):
        self.prepared(Path("fixed-inputs"), "blocked_history")
        old = (self.root / "fixed-inputs/fixed-state.json").read_bytes()

        def run(command, **kwargs):
            output = Path(command[command.index("--output-dir") + 1])
            self.assertIn("--calendar-recovery-live", command)
            self.assertEqual(command[command.index("--calendar-cache-dir") + 1], self.config["calendar_cache_dir"])
            self.prepared(output, "ready", effective={"synthetic": "recovered"})
            return SimpleNamespace(returncode=0)

        with patch.object(helper.subprocess, "run", side_effect=run) as process:
            code, result = self.invoke()
            self.assertEqual(code, 0)
            self.assertIn("fixed-inputs-recovery-", result["fixed_input_dir"])
            self.assertTrue(result["effective_snapshot_path"].endswith("effective-snapshot.json"))
            self.assertEqual((self.root / "fixed-inputs/fixed-state.json").read_bytes(), old)
            again_code, again = self.invoke()
            self.assertEqual(again_code, 0)
            self.assertEqual(again["fixed_input_dir"], result["fixed_input_dir"])
            self.assertEqual(process.call_count, 1)

    def test_changed_acquisition_or_effective_source_stops(self):
        self.prepared(Path("fixed-inputs"), "ready", effective={"synthetic": "recovered"})
        self.write("fixed-inputs/effective-snapshot.json", {"synthetic": "tampered"})
        with self.assertRaisesRegex(ValueError, "Effective source changed"):
            self.invoke()
        self.write("snapshot.json", {"synthetic": "tampered"})
        with self.assertRaisesRegex(ValueError, "Acquisition changed"):
            self.invoke()

    def test_locator_cannot_escape_request(self):
        self.write("fixed-inputs-current.json", {"request_sha256": self.sha("request.json"), "directory": "../fixed-inputs"})
        with self.assertRaisesRegex(ValueError, "Invalid preparation pointer"):
            self.invoke()

    def test_non_calendar_block_does_not_retry_network(self):
        self.prepared(Path("fixed-inputs"), "blocked_dependencies")
        with patch.object(helper.subprocess, "run") as process:
            code, _ = self.invoke()
            self.assertEqual(code, 1)
            process.assert_not_called()
