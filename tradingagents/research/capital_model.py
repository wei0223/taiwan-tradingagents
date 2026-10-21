"""Capital timeline and weighted shares, distinct from EPS-note dilution."""

from datetime import date, timedelta

from .scenarios import finite


def evaluate_dilution(parent_profit_twd, continuing_profit_twd, basic_shares, components):
    """If-converted incremental ordering using continuing-operations control EPS.

    The caller must source-review weighted increments and after-tax numerator
    adjustments; this calculation cannot discover option exercise assumptions.
    """
    parent, control, basic = map(finite, (parent_profit_twd, continuing_profit_twd, basic_shares))
    if basic <= 0 or len({c["id"] for c in components}) != len(components):
        raise ValueError("Positive basic shares and unique securities required")
    prepared = []
    for component in components:
        if not component.get("source_id") or not component.get("weighting_reviewed"):
            raise ValueError("Potential shares need reviewed time weighting and source")
        shares = finite(component["weighted_incremental_shares"])
        adjustment = finite(component["numerator_adjustment_twd"])
        if shares <= 0:
            raise ValueError("Positive incremental shares required")
        prepared.append((adjustment / shares, component["id"], shares, adjustment))
    numerator, control_num, denominator = parent, control, basic
    decisions = []
    for _, security, shares, adjustment in sorted(prepared):
        included = (
            control > 0
            and (control_num + adjustment) / (denominator + shares) < control_num / denominator
        )
        decisions.append(
            {
                "id": security,
                "included": included,
                "reason": "dilutive_to_continuing_control_eps"
                if included
                else "anti_dilutive_excluded",
            }
        )
        if included:
            numerator += adjustment
            control_num += adjustment
            denominator += shares
    return {
        "diluted_numerator_twd": numerator,
        "diluted_weighted_shares": denominator,
        "eps_twd": numerator / denominator,
        "component_decisions": decisions,
        "label": "conditional_reviewed_input_calculation_not_official_eps",
    }


def build(start, end, opening_issued, opening_treasury, events, potential=None):
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    if end < start:
        raise ValueError("Invalid share weighting period")
    issued, treasury = finite(opening_issued), finite(opening_treasury)
    if issued <= 0 or not 0 <= treasury < issued:
        raise ValueError("Invalid opening shares")
    ids, changes, unresolved = set(), [], []
    timeline = []
    for event in events:
        if event["id"] in ids:
            raise ValueError("Capital event must not be counted twice")
        ids.add(event["id"])
        if not event.get("source_id"):
            raise ValueError("Capital source required")
        timeline.append(dict(event))
        if event["kind"] in {
            "authorization",
            "approval",
            "certificate_listing",
            "stock_listing",
            "registration",
        }:
            continue  # These dates do not establish the EPS recognition date.
        if event["kind"] not in {"issue", "cancellation", "treasury_buy", "treasury_release"}:
            raise ValueError("Unknown capital event")
        day = event.get("recognition_date")
        if not day or not event.get("recognition_reviewed"):
            unresolved.append(event["id"])
            continue
        day = date.fromisoformat(day)
        if day < start:
            raise ValueError("Opening shares must already incorporate earlier recognized events")
        amount = finite(event["shares"])
        if amount <= 0:
            raise ValueError("Positive share movement required")
        if event["kind"] == "cancellation" and not isinstance(event.get("from_treasury"), bool):
            raise ValueError("Cancellation must distinguish treasury from outstanding shares")
        changes.append((day, event["id"], event["kind"], amount, event.get("from_treasury", False)))
    days = (end - start).days + 1
    weighted, day = 0.0, start
    changes.sort()
    for i in range(days):
        day = start + timedelta(days=i)
        for effective, _, kind, amount, from_treasury in changes:
            if effective == day:
                if kind == "issue":
                    issued += amount
                elif kind == "cancellation":
                    issued -= amount
                    if from_treasury:
                        treasury -= amount
                else:
                    treasury += amount if kind == "treasury_buy" else -amount
        if issued <= 0 or not 0 <= treasury < issued:
            raise ValueError("Share reconciliation fails")
        weighted += issued - treasury
    basic = None if unresolved else weighted / days
    diluted = None
    numerator_adjustment = None
    if basic is not None and potential is not None:
        if not potential.get("eps_note_reviewed") or not potential.get(
            "anti_dilution_test_reviewed"
        ):
            raise ValueError("EPS note and anti-dilution review required")
        components = potential["components"]
        if len({x["id"] for x in components}) != len(components):
            raise ValueError("Duplicate potential shares")
        recognized = {
            e.get("security_id", e["id"])
            for e in events
            if e["kind"] == "issue"
            and e.get("recognition_date")
            and date.fromisoformat(e["recognition_date"]) <= end
        }
        if any(x.get("security_id", x["id"]) in recognized or x["id"] in ids for x in components):
            raise ValueError(
                "Recognized issued shares must not also be incremental potential shares"
            )
        if any(finite(x["weighted_incremental_shares"]) < 0 for x in components):
            raise ValueError("Negative potential shares")
        diluted = basic + sum(
            finite(x["weighted_incremental_shares"]) for x in components if x["dilutive"]
        )
        numerator_adjustment = sum(
            finite(x["numerator_adjustment_twd"]) for x in components if x["dilutive"]
        )
    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "timeline": timeline,
        "unresolved_recognition": unresolved,
        "ending_issued_shares": None if unresolved else issued,
        "basic_weighted_shares": basic,
        "diluted_weighted_shares": diluted,
        "diluted_numerator_adjustment_twd": numerator_adjustment,
        "status": "unavailable_recognition"
        if unresolved
        else "basic_only"
        if diluted is None
        else "conditional_reviewed_components",
        "rule": "Issued, treasury, basic weighted and incremental potential shares are separate; never add an already diluted denominator again",
    }
