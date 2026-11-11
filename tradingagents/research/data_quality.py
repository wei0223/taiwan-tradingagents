"""Report-facing source coverage and eligible consensus comparisons."""

from collections import Counter

from .bundle import fingerprint
from .market_expectations import compare_observations
from .review import _end_of_day
from .scenarios import timestamp


def report_inputs(snapshot, audit, company, public_eps, industry):
    stock = snapshot["instrument"]["stock_id"]
    as_of = snapshot["as_of"]
    cutoff = _end_of_day(as_of)
    sources = (company or {}).get("sources", {})
    facts = (company or {}).get("facts", [])
    actuals = []
    company_lineage = []
    for fact in facts:
        source = sources[fact["source_id"]]
        known = max(timestamp(fact["available_from"]), timestamp(source["captured_at"]))
        if known > cutoff or (fact.get("period_end") or as_of) > as_of:
            raise ValueError("Future company fact in research quality inputs")
        row = {
            "fact_id": fact["id"], "metric": fact["metric"],
            "period_start": fact.get("period_start"), "period_end": fact.get("period_end"),
            "basis": fact.get("basis"), "scope": fact.get("scope"), "unit": fact.get("unit"),
            "published_at": source.get("published_at"), "captured_at": source["captured_at"],
            "available_at": known.isoformat(), "source_id": fact["source_id"],
            "source_sha256": source["sha256"], "version": source["revision_id"],
            "page": fact["page"], "source_kind": "company_disclosure" if fact["kind"] == "numeric" else "company_statement",
        }
        company_lineage.append(row)
        if fact["kind"] == "numeric" and fact["metric"] in {"basic_eps", "diluted_eps", "revenue"} and fact.get("derivation") == "direct":
            actuals.append({**row, "stock_id": stock, "value": fact["normalized_value"], "source_kind": "official_actual",
                            "first_published_at": source.get("published_at") if source.get("historical_initial_proven") else None})
    estimates = []
    for row in public_eps["vintages"]:
        if max(timestamp(row["published_at"]), timestamp(row["captured_at"])) > cutoff:
            raise ValueError("Future consensus in research quality inputs")
        year = row["fiscal_year"]
        estimates.append({
            "stock_id": stock, "metric": {"basic": "basic_eps", "diluted": "diluted_eps"}.get(row["eps_basis"], "eps"),
            "period_start": f"{year}-01-01", "period_end": f"{year}-12-31",
            "basis": "annual", "scope": row.get("scope"), "unit": f'{row["currency"]}/share' if row["currency"] else None,
            "value": row["eps"], "published_at": row["published_at"], "captured_at": row["captured_at"],
            "source_id": row["source_id"], "source_sha256": row["source_sha256"],
            "source_url": row["source_url"], "version": row["source_sha256"],
            "source_kind": "analyst_consensus", "statistic": row["statistic"], "eps_basis": row["eps_basis"],
        })
    gaps = compare_observations(stock, as_of, actuals, estimates)
    datasets = {}
    counts = Counter(row["dataset"] for row in audit["lineage"] if not row.get("published_at"))
    late = Counter(row["dataset"] for row in audit["lineage"] if timestamp(row["retrieved_at"]) > cutoff)
    for section in (snapshot["datasets"], snapshot.get("extensions", {})):
        for name, payload in section.items():
            status = "unavailable" if not payload["records"] else "ready"
            if status == "ready" and (payload["status"] != "ok" or counts[name] or late[name]):
                status = "limited"
            datasets[name] = {"status": status, "records": len(payload["records"]),
                              "publication_unknown": counts[name], "capture_after_cutoff": late[name]}
    if audit["status"] != "passed":
        status = "unavailable"
    elif (company and company["status"] == "structured_sources_available"
          and all(d["status"] == "ready" for d in datasets.values())
          and gaps["status"] == "ready"):
        status = "ready"
    else:
        status = "limited"
    quality = {
        "schema_version": 1, "stock_id": stock, "as_of": as_of, "status": status,
        "snapshot_sha256": audit["snapshot_sha256"], "source_audit_sha256": fingerprint(audit),
        "source_audit_status": audit["status"], "source_issues": audit["issues"],
        "dataset_lineage_ref": "research.json#/source_audit/lineage", "datasets": datasets,
        "company_facts": company_lineage, "company_gaps": (company or {}).get("gaps", []),
        "company_source_index_ref": "company-source-index.json" if company else None,
        "consensus_source_status": public_eps["status"], "excluded_consensus_sources": public_eps["excluded_sources"],
        "source_classes": {
            "official_actual": len(actuals), "analyst_consensus": len(estimates),
            "company_outlook": sum(s["classification"] == "management_statement" for s in industry),
            "analyst_assumption": len((company or {}).get("assumptions", []))
            + sum(s["classification"] == "analyst_assumption" for s in industry),
        },
        "expectation_gap_status": gaps["status"],
        "limitations": [*audit["limitations"], "Capture after the cutoff is a retrospective observation, not a verified point-in-time vintage."],
    }
    return quality, gaps
