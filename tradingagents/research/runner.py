"""Optional official Codex CLI entry, using ChatGPT auth only and no credential export."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .debate import load_run, stop


def launch(directory, executable="codex", execute=None):
    execute = execute or subprocess.run
    directory = Path(directory).resolve()
    state, _ = load_run(directory)
    if state["status"] == "complete":
        return 0
    env = dict(os.environ)
    # Child agents must never inherit FinMind/CLI/API secrets from the caller.
    for key in list(env):
        if key.endswith("_API_KEY") or key in {"FINMIND_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"}:
            env.pop(key)
    project = Path(__file__).resolve().parents[2]
    config = ['-c', 'forced_login_method="chatgpt"', '-c', 'model_provider="openai"']
    try:
        # Read status before forcing auth: an API-auth session is refused, not logged out.
        status = execute([executable, "login", "status"], env=env, capture_output=True,
                         text=True, encoding="utf-8", timeout=20, check=False)
        auth_text = (status.stdout or "") + (status.stderr or "")
        if status.returncode != 0 or "chatgpt" not in auth_text.lower() or "api key" in auth_text.lower():
            stop(directory, "auth")
            return 2
        prompt = (
            "Use $taiwan-stock-research at .agents/skills/taiwan-stock-research/SKILL.md "
            f"to complete the prepared research run at {directory}. "
            "The user authorizes exactly three independent subagents: bull, bear, risk. "
            "Follow the skill, import results through the Python validator, and export Traditional Chinese reports. "
            "Use existing evidence only. No API clients, fresh market data, orders, or additional agents. "
            "If quota or any required capability is unavailable, save state and stop; no provider fallback."
        )
        result = execute([executable, "exec", *config, "--sandbox", "workspace-write",
                          "--cd", str(project), "--add-dir", str(directory), "-"],
                         input=prompt, env=env, capture_output=True, text=True, encoding="utf-8",
                         timeout=900, check=False)
        current, _ = load_run(directory)
        if result.returncode != 0:
            reason = "quota" if any(word in ((result.stdout or "") + (result.stderr or "")).lower()
                                    for word in ("quota", "usage limit", "rate limit")) else "agent_failure"
            stop(directory, reason)
            return 2
        if current["status"] != "complete":
            stop(directory, "agent_failure")
            return 2
        return 0
    except subprocess.TimeoutExpired:
        stop(directory, "timeout")
        return 2
    except (OSError, ValueError):
        stop(directory, "agent_failure")
        return 2
