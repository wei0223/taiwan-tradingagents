"""Regression tests for actual dispatch envelope checks using synthetic data."""

import json
import tempfile
import unittest

from tests.taiwan.test_eight_stage import accept, fixture
from tradingagents.research import eight_stage as w
from tradingagents.research.agent_access import PROMPT_INPUT_MARKER, audit


def mutate(root, record, value):
    path = root / record["path"]
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf8")
    record["sha256"] = w.hashlib.sha256(path.read_bytes()).hexdigest()


class AgentAccessTests(unittest.TestCase):
    def test_unlisted_file(self):
        self.reject("extra_file", "allowlist")

    def test_early_opponent(self):
        self.reject("early_opponent", "unpublished")

    def test_unlisted_request_field(self):
        self.reject("hidden_payload", "Unexpected content")

    def test_extra_prompt_text(self):
        self.reject("hidden_prompt", "Unexpected text")

    def test_missing_receipt(self):
        self.reject("receipt", "Missing or reused")

    def test_inherited_conversation(self):
        self.reject("inherited", "inherited conversation")

    def reject(self, mutation, error):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        tmp_path = directory.name
        run, *_ = fixture(tmp_path)
        for job in ("fundamental", "fundamental_bull", "fundamental_bear"):
            accept(run, job)
        root, state = w.load(run)
        job = "fundamental_bull"
        entry = state["prompts"][job]
        receipt = state["completed"][job]["execution"]
        request = w.load_json(w.artifact(root, entry["request"]))
        if mutation in {"receipt", "inherited"}:
            value = w.load_json(w.artifact(root, receipt))
            if mutation == "receipt":
                value["tool_call_id"] = ""
            else:
                value["fork_turns"] = "all"
            mutate(root, receipt, value)
        else:
            if mutation == "extra_file":
                request["visible_inputs"]["technical"] = request["visible_inputs"]["financial"]
            elif mutation == "hidden_payload":
                request["opponent_analysis"] = "Private opposing opening"
            elif mutation == "early_opponent":
                other = "fundamental_bear"
                request["dependencies"][other] = state["completed"][other]["output"]["sha256"]
                request["dependency_outputs"][other] = w.result(root, state, other)
            prompt = w.artifact(root, entry["prompt"])
            body = prompt.read_text(encoding="utf8").rpartition(PROMPT_INPUT_MARKER)[0]
            if mutation == "hidden_prompt":
                body += "\nPrivate opposing opening"
            prompt.write_text(body + PROMPT_INPUT_MARKER + json.dumps(request), encoding="utf8")
            entry["prompt"]["sha256"] = w.hashlib.sha256(prompt.read_bytes()).hexdigest()
            mutate(root, entry["request"], request)
        with self.assertRaisesRegex(ValueError, error):
            audit(root, state)
