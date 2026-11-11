"""Review material claims from a source-validated workflow without editing its archive."""

from decimal import ROUND_HALF_UP, Decimal, DecimalException

from .bundle import fingerprint

STATUSES = {"supported", "challenged", "insufficient"}
LABELS = {"supported": "有證據支持", "challenged": "有爭議", "insufficient": "證據不足或待審"}


def material_claims(assembled):
    results = assembled["results"]
    findings = {f["id"]: (result, f) for result in results.values() for f in result["findings"]}
    core = [face["claim_id"] for face in assembled["workflow"]["core"]["faces"] if face["claim_id"]]
    decision = results["decision"]
    final = [f["id"] for f in decision["findings"]]
    claims = {}
    for key in dict.fromkeys([*core, *final]):
        result, finding = findings[key]
        claims[key] = {**finding, "author": result["agent_id"], "job": result["job"],
                       "prompt_sha256": result["prompt_sha256"], "use": "core" if key in core else "decision"}
    for ruling in decision["payload"]["rulings"]:
        key = "decision.ruling_" + ruling["axis"]
        claims[key] = {
            "id": key, "claim": ruling["reason"], "author": decision["agent_id"], "job": "decision",
            "prompt_sha256": decision["prompt_sha256"], "use": "ruling",
            "horizon": "short_term" if ruling["axis"] == "short_term" else "unspecified",
            "evidence": ruling["evidence"], "counterargument": decision["payload"]["key_disagreement"],
            "revision_condition": "。".join(decision["conditions"]), "original_verdict": ruling["verdict"],
        }
    return claims


def verify_numeric_checks(claim, checks):
    """Evaluate reviewer-selected operations against archived scalar evidence."""
    if not isinstance(checks, list) or len(checks) > 12:
        raise ValueError("Bounded numeric checks required")
    for check in checks:
        if not isinstance(check, dict) or set(check) != {"operation", "evidence_indices", "expected", "places"}:
            raise ValueError("Invalid numeric check fields")
        operation, indices, places = check["operation"], check["evidence_indices"], check["places"]
        size = {"value": 1, "difference": 2, "percent_change": 2}.get(operation)
        if (size is None or not isinstance(indices, list) or len(indices) != size
                or any(type(i) is not int or not 0 <= i < len(claim["evidence"]) for i in indices)
                or type(places) is not int or not 0 <= places <= 6):
            raise ValueError("Numeric check must refer to approved evidence and precision")
        try:
            values = [Decimal(claim["evidence"][i]["quote"]) for i in indices]
            expected = Decimal(str(check["expected"]))
            if not all(x.is_finite() for x in [*values, expected]):
                raise ValueError("Numeric check must use finite values")
            if operation == "value":
                calculated = values[0]
            elif operation == "difference":
                calculated = values[0] - values[1]
            else:
                calculated = (values[0] / values[1] - 1) * 100
            if calculated.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP) != expected:
                raise ValueError("Numeric check differs from archived evidence")
        except DecimalException as error:
            raise ValueError("Invalid numeric operation on evidence") from error


def review(assembled, packet=None):
    claims = material_claims(assembled)
    stock, as_of = assembled["stock_id"], assembled["as_of"]
    if packet is not None:
        if (packet.get("stock_id"), packet.get("as_of")) != (stock, as_of):
            raise ValueError("Reasoning review issuer/cutoff mismatch")
        assessments = packet["assessments"]
        if not isinstance(assessments, dict) or set(assessments) - claims.keys():
            raise ValueError("Review must name existing material claims")
    else:
        assessments = {}
    rows = {}
    for key, claim in claims.items():
        digest = fingerprint(claim)
        entry = assessments.get(key)
        if key in assessments:
            required = {"claim_sha256", "reviewer", "status", "inference", "reason"}
            if not isinstance(entry, dict) or not required <= entry.keys() <= required | {"numeric_checks"} or entry["claim_sha256"] != digest:
                raise ValueError("Reasoning review is not bound to the archived claim")
            if entry["status"] not in STATUSES or any(
                not isinstance(entry[field], str) or not entry[field].strip()
                for field in ("reviewer", "inference", "reason")
            ):
                raise ValueError("A named reviewer, inference and rationale are required")
            if entry["reviewer"].strip().casefold() == claim["author"].strip().casefold():
                raise ValueError("Claim author cannot independently review the claim")
            verify_numeric_checks(claim, entry.get("numeric_checks", []))
            if entry["status"] == "supported" and (
                not claim["evidence"] or claim.get("original_verdict") in {"uncertain", "unavailable"}
            ):
                raise ValueError("An unsupported or unavailable ruling cannot become confirmed")
        else:
            entry = {"status": "insufficient", "reviewer": None, "inference": None,
                     "reason": "尚無獨立推論審查。"}
        status = entry["status"]
        rows[key] = {
            **claim, **entry, "claim_sha256": digest, "confirmed_core": status == "supported" and claim["use"] == "core",
            "source_binding": "validated_workflow_references",
            "next_action": "retry_required" if status == "challenged" else
                           "review_required" if status == "insufficient" else None,
            "return_to": claim["author"] if status == "challenged" else None,
        }
    summary = {
        "schema_version": 1, "stock_id": stock, "as_of": as_of,
        "status": "passed" if rows and all(r["status"] == "supported" for r in rows.values()) else "limited",
        "counts": {status: sum(r["status"] == status for r in rows.values()) for status in sorted(STATUSES)},
        "scope": "selected core claims, final findings and ruling axes",
        "semantic_verdict_source": "named reviewer judgment",
        "semantic_truth_verified_by_program": False,
        "original_agent_outputs_modified": False,
    }
    return summary, {"schema_version": 1, "stock_id": stock, "as_of": as_of, "claims": rows}


def display_rows(validation):
    return [[row["id"], LABELS[row["status"]], row["reason"]] for row in validation["claims"].values()]
