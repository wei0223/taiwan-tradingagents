"""Offline revenue experiments. Input rows must come from verified source vintages."""

import argparse
import json
from pathlib import Path

from .predictive import METHODS, calibrated_weights, estimate, walk_forward


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--observations", type=Path, required=True)
    p.add_argument("--cutoff", required=True)
    p.add_argument("--targets", required=True, help="Comma-separated YYYY-MM target months")
    p.add_argument("--folds", type=Path, help="Historical cutoff/targets list")
    p.add_argument(
        "--industry", type=Path, help="Source-linked current independent industry drivers"
    )
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args(argv)
    rows = json.loads(a.observations.read_text(encoding="utf-8"))
    industry = json.loads(a.industry.read_text(encoding="utf-8")) if a.industry else None
    folds = json.loads(a.folds.read_text(encoding="utf-8")) if a.folds else []
    validation = walk_forward(rows, folds, a.cutoff)
    result = {
        "cutoff": a.cutoff,
        "methods": {
            k: estimate(rows, a.cutoff, a.targets.split(","), k, industry) for k in METHODS
        },
        "validation": validation,
        "weights": calibrated_weights(validation, a.cutoff),
        "orders": False,
        "llm_api_calls": False,
        "source_trust_boundary": "Observation lineage must be verified against archived raw sources by caller",
    }
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
