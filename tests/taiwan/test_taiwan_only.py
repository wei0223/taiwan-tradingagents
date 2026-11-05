"""Protect the intentional Taiwan-only package boundary and executable entry points."""

import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TaiwanOnlyTests(unittest.TestCase):
    def command(self, *args):
        return subprocess.run(
            [sys.executable, *args], cwd=ROOT, capture_output=True,
            text=True, encoding="utf8", timeout=60, check=True,
        ).stdout

    def test_install_has_only_data_dependencies_and_taiwan_entries(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text("utf8"))
        project = config["project"]
        self.assertEqual(project["dependencies"], ["python-dotenv>=1.0.0", "requests>=2.32.4"])
        self.assertEqual(project["scripts"], {
            "tradingagents": "tradingagents.research.__main__:main",
            "taiwan-research": "tradingagents.research.__main__:main",
        })
        self.assertNotIn("bedrock", project["optional-dependencies"])
        self.assertEqual(config["tool"]["setuptools"]["packages"]["find"]["include"], ["tradingagents*"])

    def test_removed_runtime_files_are_absent(self):
        for name in ("cli", "tradingagents/agents", "tradingagents/graph", "tradingagents/llm_clients", "tradingagents/memory", "tradingagents/dataflows/vendors"):
            self.assertFalse(list((ROOT / name).rglob("*.py")), name)
        for name in ("backtest", "portfolio", "default_config", "reporting", "report_html"):
            self.assertFalse((ROOT / "tradingagents" / (name + ".py")).exists())

    def test_all_taiwan_modules_import_without_removed_sdk_stack(self):
        output = self.command("-c", '''
import importlib, importlib.abc, pkgutil, sys
class BlockLegacy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ('langchain', 'langgraph', 'yfinance', 'stockstats', 'rich', 'typer', 'questionary', 'openai', 'anthropic', 'cli')
        if any(fullname == x or fullname.startswith(x + '.') or fullname.startswith(x + '_') for x in blocked):
            raise AssertionError('Removed dependency imported: ' + fullname)
sys.meta_path.insert(0, BlockLegacy())
import tradingagents
modules = list(pkgutil.walk_packages(tradingagents.__path__, tradingagents.__name__ + '.'))
for info in modules:
    importlib.import_module(info.name)
assert {"tradingagents.dataflows.taiwan.provider", "tradingagents.research.agent_protocol", "tradingagents.research.reporting", "tradingagents.research.publication"} <= {info.name for info in modules}
print('Taiwan modules imported without legacy SDKs')
''')
        self.assertIn("Taiwan modules imported without legacy SDKs", output)

    def test_module_cli_lists_fixed_research_and_legacy_release_reader(self):
        output = self.command("-m", "tradingagents.research", "--help")
        for command in ("prepare", "fixed-inputs", "agent-prompt", "upgrade", "track-release"):
            self.assertIn(command, output)

    def test_main_script_is_help_only_without_a_stock_request(self):
        output = self.command("main.py", "--help")
        self.assertIn("company-plan", output)
        self.assertNotIn("NVDA", output)


if __name__ == "__main__":
    unittest.main()
