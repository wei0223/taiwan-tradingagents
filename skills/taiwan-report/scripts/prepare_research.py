"""Prepare one existing request, recovering verified calendar originals when needed."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def read(path):
    return json.loads(path.read_text("utf8"))


def validate(output, snapshot, config, protocol):
    saved = read(output / "fixed-state.json")
    if saved.get("agent_protocol") != protocol:
        raise ValueError("Preparation protocol changed; create a new request")
    technical, company = read(output / "technical-package.json"), read(output / "company-plan.json")
    if digest(technical) != saved["technical_sha256"] or digest(company) != saved["company_plan_sha256"]:
        raise ValueError("Prepared calculation changed")
    if digest(snapshot) != saved.get("acquisition_snapshot_sha256", saved["source_snapshot_sha256"]):
        raise ValueError("Preparation bound to another acquisition")
    effective = output / saved.get("effective_snapshot", "effective-snapshot.json")
    if saved.get("effective_snapshot") and digest(read(effective)) != saved["source_snapshot_sha256"]:
        raise ValueError("Effective source changed")
    implementation = Path(config["repository"]) / "tradingagents/research/technical_engine.py"
    if hashlib.sha256(implementation.read_bytes()).hexdigest() != technical["implementation_sha256"]:
        raise ValueError("Engine changed; create a new preparation edition")
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--calendar-seed-snapshot", type=Path, action="append", default=[])
    args = parser.parse_args()
    config = read(Path(__file__).with_name("project.json"))
    request_path = args.request.resolve()
    if os.name == "nt" and not str(request_path).startswith("\\\\?\\"):
        request_path = Path("\\\\?\\" + str(request_path))
    request = read(request_path)
    protocol = request.get("upgrade_extension_defaults", {}).get("agent_protocol", "fixed-research-agents-v1")
    if protocol not in {"fixed-research-agents-v1", "taiwan-eight-stage-v2"}:
        raise ValueError("Unknown request protocol")
    root = request_path.parent
    state = read(root / "preparation.json")
    request_sha = hashlib.sha256(request_path.read_bytes()).hexdigest()
    if request_sha != state["request_sha256"] or not state["status"].startswith("data_available"):
        raise ValueError("Matching live data required")
    snapshot = root / state["snapshot"]
    if hashlib.sha256(snapshot.read_bytes()).hexdigest() != state["snapshot_sha256"]:
        raise ValueError("Acquisition changed")
    source = read(snapshot)
    output = root / "fixed-inputs"
    pointer = root / "fixed-inputs-current.json"
    if pointer.exists():
        selected = read(pointer)
        relative = Path(selected["directory"])
        if selected["request_sha256"] != request_sha or relative.is_absolute() or len(relative.parts) != 1:
            raise ValueError("Invalid preparation pointer")
        if relative.name in {".", ".."} or not relative.name.startswith("fixed-inputs"):
            raise ValueError("Invalid preparation directory")
        output = root / relative
    if output.exists():
        saved = validate(output, source, config, protocol)
        if saved["technical_status"] != "blocked_history":
            saved["fixed_input_dir"] = str(output)
            saved["effective_snapshot_path"] = str(output / saved["effective_snapshot"]) if saved.get("effective_snapshot") else str(snapshot)
            print(json.dumps(saved))
            return 0 if saved["technical_status"] == "ready" else 1
        # Never replace old calculations or an agent run. Recovery is a new edition.
        output = root / ("fixed-inputs-recovery-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env["PYTHONPATH"] = os.pathsep.join([
        str(config["repository"]), config.get("technical_dependency_path", ""),
        config.get("base_dependency_path", ""), env.get("PYTHONPATH", ""),
    ])
    cache = config.get("calendar_cache_dir", str(Path(config["repository"]).parent / "calendar-cache"))
    command = [sys.executable, "-X", "utf8", "-m", "tradingagents.research", "fixed-inputs",
               "--agent-protocol", protocol, "--snapshot", str(snapshot), "--output-dir", str(output),
               "--calendar-cache-dir", cache, "--calendar-recovery-live"]
    for seed in [*config.get("calendar_seed_snapshots", []), *args.calendar_seed_snapshot]:
        command.extend(["--calendar-seed-snapshot", str(seed)])
    result = subprocess.run(command, env=env, check=False, capture_output=True, text=True, encoding="utf8")
    if not (output / "fixed-state.json").exists():
        sys.stderr.write(result.stderr)
        sys.stdout.write(result.stdout)
        return result.returncode or 1
    saved = validate(output, source, config, protocol)
    # Updating this locator never changes an existing report, source, or calculation.
    pointer.write_text(json.dumps({"directory": output.name, "request_sha256": request_sha}, indent=2) + "\n", encoding="utf8")
    saved["fixed_input_dir"] = str(output)
    saved["effective_snapshot_path"] = str(output / saved["effective_snapshot"])
    print(json.dumps(saved))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
