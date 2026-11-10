"""Freeze and compare four research modes on common evidence and review criteria."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from .bundle import canonical, fingerprint
from .delivery import read_bytes
from .forecast_journal import read_research
from .scenarios import finite, timestamp

MODES = {
    "single_ai": ("integrated_analysis",),
    "three_dimensions": ("fundamental", "technical", "institutional", "synthesis"),
    "three_dimensions_debate": ("fundamental", "technical", "institutional", "bull_opening",
                                "bear_opening", "bull_reply", "bear_reply", "synthesis"),
    "full_eight_stage": ("sealed_eight_stage_workflow",),
}
RUBRIC = {
    "source_integrity": "0 missing/invalid, 1 partial, 2 major facts traceable and timely",
    "evidence_completeness": "0 missing material evidence, 1 partial, 2 major claims and counterevidence covered",
    "reasoning_accuracy": "0 material error, 1 bounded concern, 2 no identified material inference error",
    "material_omissions": "0 major unresolved omissions, 1 partial, 2 material gaps are handled or disclosed",
}
INPUTS = ("source_snapshot", "company", "technical", "financial", "public_eps", "dynamics")


def _load(path):
    return json.loads(read_bytes(path))


def _write(path, value):
    with path.open("x", encoding="utf8") as out:
        out.write(canonical(value) + "\n")


def _check_hash(value, name):
    if value[name] != fingerprint({k: v for k, v in value.items() if k != name}):
        raise ValueError("Experiment record hash mismatch: " + name)


def freeze(calibration, holdout, output, model=None, randomness=None, clock=None):
    now = (clock or (lambda: datetime.now(UTC)))()
    cells, pilots, captures, identities, snapshots = {}, [], {}, set(), set()
    if now.tzinfo is None:
        raise ValueError("Experiment freeze needs a timezone")
    for split, directories in (("calibration", calibration), ("holdout", holdout)):
        for directory in directories:
            root = Path(directory)
            record, manifest = read_research(root)
            identity = (record["stock_id"], record["data_as_of"])
            if identity in identities or record["snapshot_sha256"] in snapshots:
                raise ValueError("Repeated issuer/date or snapshot across experiment cells")
            identities.add(identity)
            snapshots.add(record["snapshot_sha256"])
            state = _load(root / "agent-run/state.json")
            inputs = state["revisions"][0]["inputs"]
            sealed_inputs = {k: inputs[k]["sha256"] for k in INPUTS}
            cell = {"stock_id": identity[0], "as_of": identity[1], "split": split,
                    "input_sha256": fingerprint(sealed_inputs), "inputs": sealed_inputs,
                    "seed_research_manifest_sha256": manifest["manifest_sha256"]}
            key = fingerprint(cell)
            cells[key] = cell
            captures[key] = {k: root / "agent-run" / inputs[k]["path"] for k in sealed_inputs}
            audit = _load(root / "agent-execution-audit.json")
            kinds = [job["execution_kind"] for job in audit["jobs"]]
            pilots.append({
                "cell_id": key, "mode": "full_eight_stage",
                "origin": "archived_workflow_pilot_before_experiment_freeze",
                "dispatches": sum(k in {"spawn_agent", "followup_task"} for k in kinds),
                "accepted_jobs": len(kinds), "unavailable_jobs": kinds.count("unavailable"),
                "research_id": record["research_id"], "model_version": record["codex_model_version"],
                "elapsed_seconds": None, "retries": None, "token_usage": None, "api_cost": None,
                "comparative_quality": None, "performance_eligible": False,
            })
    if not cells:
        raise ValueError("Declare at least one comparison cell")
    plan = {"schema_version": 1, "frozen_at": now.isoformat(), "cells": cells,
            "modes": MODES, "rubric": RUBRIC, "rubric_sha256": fingerprint(RUBRIC),
            "pilots_sha256": fingerprint(pilots),
            "model_control": model, "randomness_control": randomness,
            "constraints": {"supplement": "reuse only the same frozen inputs",
                            "opening": "fresh blind context; exchange arguments after both openings",
                            "output": "same issuer/cutoff, claims, evidence, counterevidence, limits and invalidation",
                            "holdout": "no reuse of calibration cells; archived pilots are descriptive only"}}
    plan["plan_sha256"] = fingerprint(plan)
    root = Path(output)
    root.mkdir(parents=True)
    for key, captured in captures.items():
        folder = root / "inputs" / key
        folder.mkdir(parents=True)
        for name, path in captured.items():
            content = read_bytes(path)
            if hashlib.sha256(content).hexdigest() != cells[key]["inputs"][name]:
                raise ValueError("Frozen input bytes changed")
            (folder / (name + ".json")).write_bytes(content)
    _write(root / "ablation-plan.json", plan)
    _write(root / "ablation-pilots.json", pilots)
    return plan


def _quality(review, run, plan):
    if review is None:
        return None
    if (review.get("output_sha256") != run["output_sha256"]
            or review.get("rubric_sha256") != plan["rubric_sha256"]
            or review.get("blind") is not True or not review.get("reviewer")):
        raise ValueError("Blind review must bind the output and frozen rubric")
    ratings = review["ratings"]
    if set(ratings) != RUBRIC.keys() or any(type(v) is not int or not 0 <= v <= 2 for v in ratings.values()):
        raise ValueError("Explicit 0–2 rating required for every rubric criterion")
    for key in ("inference_errors", "material_omissions"):
        if type(review[key]) is not int or review[key] < 0:
            raise ValueError("Explicit nonnegative review counts required")
    return {"reviewer": review["reviewer"], "ratings": ratings,
            "score": sum(ratings.values()), "inference_errors": review["inference_errors"],
            "material_omissions": review["material_omissions"]}


def _metric(run, name, integer=False):
    value = run.get(name)
    if value is not None and (finite(value) < 0 or integer and type(value) is not int):
        raise ValueError("Invalid observed metric: " + name)
    return value


def compare(plan, runs, pilots=()):
    _check_hash(plan, "plan_sha256")
    accepted = {}
    for run in runs:
        cell = plan["cells"].get(run["cell_id"])
        key = (run["cell_id"], run["mode"])
        if (cell is None or run["mode"] not in MODES or key in accepted
                or run["plan_sha256"] != plan["plan_sha256"]
                or run["input_sha256"] != cell["input_sha256"]):
            raise ValueError("Mode result does not match the frozen comparison cell")
        _check_hash(run, "run_sha256")
        started = timestamp(run["started_at"])
        finished = timestamp(run["finished_at"])
        if finished < started or started < timestamp(plan["frozen_at"]):
            raise ValueError("Comparison run must start after experiment freeze")
        if run["status"] not in {"completed", "failed"}:
            raise ValueError("Explicit mode completion status required")
        if run["status"] == "completed":
            result = run.get("output")
            fields = {"stock_id", "as_of", "stance", "summary", "claims", "counterevidence", "limits", "invalidation"}
            if (not isinstance(result, dict) or set(result) != fields
                    or (result["stock_id"], result["as_of"]) != (cell["stock_id"], cell["as_of"])
                    or fingerprint(result) != run["output_sha256"]):
                raise ValueError("Mode output must match the shared issuer/cutoff and contract")
        controlled = (
            plan["model_control"] is not None and plan["randomness_control"] is not None
            and run.get("model_control") == plan["model_control"]
            and run.get("randomness_control") == plan["randomness_control"]
        )
        quality = _quality(run.get("review"), run, plan) if run["status"] == "completed" else None
        accepted[key] = {
            "status": run["status"], "run_sha256": run["run_sha256"],
            "controlled": controlled, "quality": quality, "elapsed_seconds": (finished - started).total_seconds(),
            "dispatches": _metric(run, "dispatches", True), "retries": _metric(run, "retries", True),
            "token_usage": None, "api_cost": None, "predictive_performance": "unavailable",
        }
    rows, paired = [], []
    for key, cell in plan["cells"].items():
        modes = {mode: accepted.get((key, mode), {"status": "pending"}) for mode in MODES}
        row = {"cell_id": key, "stock_id": cell["stock_id"], "as_of": cell["as_of"],
               "split": cell["split"], "modes": modes}
        rows.append(row)
        if (all(v["status"] == "completed" and v["controlled"] and v["quality"] for v in modes.values())
                and len({v["quality"]["reviewer"] for v in modes.values()}) == 1):
            paired.append(key)
    holdout = [key for key in paired if plan["cells"][key]["split"] == "holdout"]
    mode_totals = {}
    paired_means = {}
    for mode in MODES:
        observed = [r["modes"][mode] for r in rows if r["modes"][mode]["status"] != "pending"]
        mode_totals[mode] = {"observed_runs": len(observed),
                             "completed": sum(x["status"] == "completed" for x in observed),
                             "failed": sum(x["status"] == "failed" for x in observed),
                             "success_rate": sum(x["status"] == "completed" for x in observed) / len(observed) if observed else None}
        selected = [accepted[key, mode] for key in holdout]
        groups = {k: [r[k] for r in selected] for k in ("elapsed_seconds", "dispatches", "retries")}
        groups.update({k: [r["quality"][k] for r in selected] for k in ("score", "inference_errors", "material_omissions")})
        paired_means[mode] = {k: sum(values) / len(values) if values and all(x is not None for x in values) else None
                              for k, values in groups.items()}
    return {
        "plan_sha256": plan["plan_sha256"], "cells": rows, "pilot_observations": list(pilots),
        "paired_cells": paired, "paired_holdout_cells": holdout, "mode_totals": mode_totals,
        "paired_holdout_means": paired_means,
        "status": "descriptive_paired_quality" if holdout else "insufficient_comparable_holdout",
        "historical_performance": "unavailable_no_verified_prospective_four_mode_pairs",
        "research_benefit_proven": False,
    }


def compare_files(plan_path, run_paths, output):
    plan = _load(plan_path)
    _check_hash(plan, "plan_sha256")
    for key, cell in plan["cells"].items():
        if fingerprint(cell) != key or set(cell["inputs"]) != set(INPUTS):
            raise ValueError("Experiment cell identity changed")
        for name, digest in cell["inputs"].items():
            file = Path(plan_path).parent / "inputs" / key / (name + ".json")
            if hashlib.sha256(read_bytes(file)).hexdigest() != digest:
                raise ValueError("Experiment input package changed")
    pilot_path = Path(plan_path).with_name("ablation-pilots.json")
    pilots = _load(pilot_path)
    if fingerprint(pilots) != plan["pilots_sha256"]:
        raise ValueError("Pilot observations changed")
    result = compare(plan, [_load(path) for path in run_paths], pilots)
    root = Path(output)
    root.mkdir(parents=True)
    _write(root / "ablation-results.json", result)
    _write(root / "research-mode-comparison.json", {k: v for k, v in result.items() if k != "cells"})
    return {"status": result["status"], "cells": len(result["cells"]),
            "paired_holdout_cells": len(result["paired_holdout_cells"])}
