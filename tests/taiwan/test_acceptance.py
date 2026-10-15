"""Same offline preparation-to-report contract for different companies."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.taiwan.test_extensions import make_provider
from tradingagents.research.__main__ import main


class AcceptanceTests(unittest.TestCase):
    def test_prepared_companies_use_same_cli_and_fail_closed_on_mops_gap(self):
        for code in ("3017", "3231"):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as folder:
                provider, _ = make_provider()
                snapshot = provider.snapshot(code, "2024-01-01", "2024-05-20")
                path = Path(folder) / "snapshot.json"
                output = Path(folder) / "integrated"
                path.write_text(json.dumps(snapshot), encoding="utf-8")
                self.assertEqual(main(["acceptance", "--snapshot", str(path), "--output-dir", str(output)]), 0)
                result = json.loads((output / "acceptance.json").read_text(encoding="utf-8"))
                self.assertEqual(result["stock_id"], code)
                self.assertFalse(result["llm_api_calls"])
                self.assertTrue((output / "report.html").is_file())
                snapshot["publication_audit"][0]["response_sha256"] = "0" * 64
                path.write_text(json.dumps(snapshot), encoding="utf-8")
                self.assertEqual(main(["acceptance", "--snapshot", str(path), "--output-dir", str(output)]), 1)
                snapshot["publication_audit"] = []
                path.write_text(json.dumps(snapshot), encoding="utf-8")
                self.assertEqual(main(["acceptance", "--snapshot", str(path), "--output-dir", str(output)]), 1)
