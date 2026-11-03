"""Run deterministic preparation for the existing request; never dispatch a second workflow."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(Path(__file__).with_name("project.json").read_text("utf8"))
    request_path = args.request.resolve()
    if os.name == "nt" and not str(request_path).startswith("\\\\?\\"):
        request_path = Path("\\\\?\\" + str(request_path))
    json.loads(request_path.read_text("utf8"))
    root = request_path.parent
    state = json.loads((root / "preparation.json").read_text("utf8"))
    if hashlib.sha256(request_path.read_bytes()).hexdigest() != state[
        "request_sha256"
    ] or not state["status"].startswith("data_available"):
        raise ValueError("Matching live data required")
    snapshot = root / state["snapshot"]
    if hashlib.sha256(snapshot.read_bytes()).hexdigest() != state["snapshot_sha256"]:
        raise ValueError("Snapshot changed")
    output = root / "fixed-inputs"
    if output.exists():
        saved = json.loads((output / "fixed-state.json").read_text("utf8"))

        def canonical(v):
            return json.dumps(
                v, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()

        technical = json.loads((output / "technical-package.json").read_text("utf8"))
        company = json.loads((output / "company-plan.json").read_text("utf8"))
        if (
            hashlib.sha256(canonical(technical)).hexdigest() != saved["technical_sha256"]
            or hashlib.sha256(canonical(company)).hexdigest() != saved["company_plan_sha256"]
        ):
            raise ValueError("Prepared fixed calculation changed")
        if (
            hashlib.sha256(canonical(json.loads(snapshot.read_text("utf8")))).hexdigest()
            != saved["source_snapshot_sha256"]
        ):
            raise ValueError("Fixed package bound to another source")
        implementation = Path(config["repository"]) / "tradingagents/research/technical_engine.py"
        if (
            hashlib.sha256(implementation.read_bytes()).hexdigest()
            != technical["implementation_sha256"]
        ):
            raise ValueError("Engine changed; create a new preparation edition")
        print(json.dumps(saved))
        return 0 if saved["technical_status"] == "ready" else 1
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(config["repository"]),
            config.get("technical_dependency_path", ""),
            config.get("base_dependency_path", ""),
            env.get("PYTHONPATH", ""),
        ]
    )
    return subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "tradingagents.research",
            "fixed-inputs",
            "--snapshot",
            str(snapshot),
            "--output-dir",
            str(output),
        ],
        env=env,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
