"""Offline transport tests: no GitHub, Sponsor quota or authentication required."""
import importlib.util
import json
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime
from pathlib import Path

SCRIPT = Path(__file__).with_name("prepare_data.py")
if not SCRIPT.exists():
    SCRIPT = Path(__file__).resolve().parents[2] / "skills/taiwan-report/scripts/prepare_data.py"
spec = importlib.util.spec_from_file_location("prepare_data_skill", SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
NOW = datetime(2026, 11, 1, 14, 15, tzinfo=UTC)


class FakeGh:
    def __init__(self):
        self.auth = True
        self.dispatches = 0
        self.downloads = 0
        self.run = {"id": 99, "head_branch": "main", "head_sha": "a" * 40,
                        "event": "workflow_dispatch", "actor": {"login": "owner"},
                        "created_at": NOW.isoformat(), "status": "in_progress", "conclusion": None}
        self.extra = []
        self.fail_dispatch = False
        self.artifacts = [{"id": 5, "name": "taiwan-research-data-99", "expired": False}]
        self.stock_id = "3017"
        self.date = "2026-11-01"
        self.capture = NOW.isoformat()

    def authenticated(self):
        return self.auth

    def api(self, endpoint):
        if endpoint == "user":
            return {"login": "owner"}
        if "git/ref" in endpoint:
            return {"object": {"sha": "a" * 40}}
        if "workflows/" in endpoint:
            return {"workflow_runs": ([self.run] + self.extra) if self.dispatches else []}
        if endpoint.endswith("/artifacts"):
            return {"artifacts": self.artifacts}
        return self.run

    def dispatch(self, inputs):
        self.dispatches += 1
        if self.fail_dispatch:
            raise m.TransportError("uncertain response")

    def download(self, artifact_id, output):
        self.downloads += 1
        snapshot = {"instrument": {"stock_id": self.stock_id}, "as_of": self.date,
                        "datasets": {"price": {"retrieved_at": self.capture}}}
        with zipfile.ZipFile(output, "w") as bundle:
            for filename in ["3017-2026-11-01/snapshot.json", "3017-2026-11-01/integrated/snapshot.json"]:
                bundle.writestr(filename, json.dumps(snapshot))


class OnDemandTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "request.json"
        self.request = {"stock_id": "3017", "symbol": "3017", "as_of": "2026-11-01",
                            "workflow": {"repository": m.REPOSITORY, "file": m.WORKFLOW, "ref": "main",
                                          "inputs": {"symbols": "3017", "as_of": "2026-11-01"}}}
        self.path.write_text(json.dumps(self.request), encoding="utf-8")
        self.client = FakeGh()

    def prepare(self):
        return m.prepare(self.path, self.client, clock=lambda: NOW)

    def test_missing_auth_never_dispatches(self):
        self.client.auth = False
        self.assertEqual(self.prepare()["status"], "github_cli_authentication_required")
        self.assertEqual(self.client.dispatches, 0)

    def test_wait_resume_and_cached_data_do_not_dispatch_again(self):
        self.assertEqual(self.prepare()["status"], "awaiting_workflow_completion")
        self.client.run.update(status="completed", conclusion="success")
        ready = self.prepare()
        self.assertEqual(ready["status"], "data_available")
        self.assertFalse(ready["report_generated"])
        self.assertEqual(self.prepare(), ready)
        self.assertEqual((self.client.dispatches, self.client.downloads), (1, 1))

    def test_uncertain_dispatch_reconciles_without_resending(self):
        self.client.fail_dispatch = True
        self.assertEqual(self.prepare()["status"], "dispatch_uncertain_do_not_redispatch")
        self.assertEqual(self.prepare()["status"], "awaiting_workflow_completion")
        self.assertEqual(self.client.dispatches, 1)

    def test_ambiguous_runs_not_latest_success(self):
        self.client.extra = [dict(self.client.run, id=100)]
        self.assertEqual(self.prepare()["status"], "ambiguous_runs_requires_review")
        self.assertNotIn("run_id", self.prepare())
        self.assertEqual(self.client.dispatches, 1)

    def test_no_matching_run_is_waiting_not_new_dispatch(self):
        self.client.run["actor"] = {"login": "someone-else"}
        self.assertEqual(self.prepare()["status"], "awaiting_run")
        self.prepare()
        self.assertEqual(self.client.dispatches, 1)

    def test_wrong_bound_branch_rejected(self):
        self.prepare()
        self.client.run["head_branch"] = "feature"
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(self.client.dispatches, 1)

    def test_failed_workflow_with_real_snapshot_retains_warning(self):
        self.client.run.update(status="completed", conclusion="failure")
        self.assertEqual(self.prepare()["status"], "data_available_with_workflow_warnings")

    def test_missing_artifact_is_explicit(self):
        self.client.run.update(status="completed", conclusion="failure")
        self.client.artifacts = []
        self.assertEqual(self.prepare()["status"], "workflow_finished_without_usable_artifact")
        self.assertEqual(self.client.downloads, 0)

    def test_wrong_company_and_date_rejected(self):
        self.client.run.update(status="completed", conclusion="success")
        self.client.stock_id = "2330"
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(self.client.dispatches, 1)

    def test_stale_snapshot_cannot_complete_live_request(self):
        self.client.run.update(status="completed", conclusion="success")
        self.client.capture = "2026-10-01T00:00:00+00:00"
        with self.assertRaises(ValueError):
            self.prepare()

    def test_mutated_request_and_prepared_snapshot_rejected(self):
        self.client.run.update(status="completed", conclusion="success")
        ready = self.prepare()
        (self.root / ready["snapshot"]).write_text("{}")
        with self.assertRaises(ValueError):
            self.prepare()
        self.request["extra"] = "changed"
        self.path.write_text(json.dumps(self.request))
        with self.assertRaises(ValueError):
            self.prepare()

    def test_untrusted_workflow_rejected_before_dispatch(self):
        self.request["workflow"]["ref"] = "feature"
        self.path.write_text(json.dumps(self.request))
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(self.client.dispatches, 0)

    def test_archive_traversal_and_case_duplicate_rejected_before_writes(self):
        for names in [["../outside"], ["/absolute"], ["C:/outside"], ["file", "FILE"]]:
            archive = self.root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                for name in names:
                    bundle.writestr(name, "test")
            with self.assertRaises(ValueError):
                m.extract_archive(archive, self.root / "extract")
            self.assertFalse((self.root / "extract").exists())

    def test_timezone_is_required(self):
        with self.assertRaises(ValueError):
            m.prepare(self.path, self.client, clock=lambda: datetime(2026, 11, 1))


if __name__ == "__main__":
    unittest.main()
