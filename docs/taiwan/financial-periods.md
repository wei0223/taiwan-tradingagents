# Financial field periods and version-safe conversion

Quarter-end dates are observations, not proof of a single-quarter field. FinMind
June 30 amounts can be H1 while `EPS` is quarterly **basic** EPS. Raw API rows and
their original values remain untouched. `field_periods` on vendor records records
unknown start/end/scope, the vendor's date/unit label, source identity, publication
and retrieval time. Publication proof alone never certifies accounting periods.

The default provider, imported-snapshot bundle, extended cash-flow calculations and
daily report now fail closed: unreviewed financial periods yield null ratios/EPS,
and research requiring quarterly earnings remains `insufficient_data`. Cached
`quarterly_financials` or a forged vendor `field_periods.reviewed` flag cannot bypass
the original-review path. US providers are unchanged.

## Daily entry points

The existing `upgrade --review-input ... --extension-input ...` path adapts reviewed
`financial_packets`, earnings notes and cash YTD packets automatically. It uses
their original bytes, company, scope, TWD/share normalization, page locator, named
reviewer, selected version and cutoff. It is not automatic PDF semantic extraction.
Additional directly reviewed EPS disclosures may be supplied as
`review_input.financial_period_observations`; they use the same contract below.
Extension financial packets additionally require an explicit `statement_kind`
(`income_statement`, `cash_flow_statement`, `eps_note`) or a reviewed
`review_input.financial_period_packet_selection` mapping from packet name to kind.
Do not label quarterly sums of monthly revenue as income-statement packets.
Monthly totals remain usable in the separately identified monthly revenue model,
but cannot fill a missing quarterly financial-statement denominator.

For data-only preparation, a standalone ledger is also supported:

```console
python -m tradingagents.dataflows.taiwan --symbol 3017 --start 2025-01-01 --as-of 2026-10-25 --financial-period-review reviewed-3017.json --output snapshot.json
python -m tradingagents.research prepare --symbols 2330,3017,3231 --start 2025-01-01 --as-of 2026-10-25 --output-dir new-run --financial-period-review-dir reviewed-ledgers
```

The latter directory contains `2330.json`, `3017.json`, `3231.json`. Missing reviews
remain unavailable. Provider callers may inject a ledger or a `(stock, as_of)`
resolver via `financial_period_review`; mocks use the same validation.

## Ledger contract

A ledger contains `stock_id`, `documents`, `observations`, and optional `bridges`.
Each document needs company, HTTPS source URL, revision ID, original
`content_base64`, SHA256, precise capture timestamp and publication/public-capture
proof. An unknown publication time permits current knowledge from verified capture
only, never an inferred historical release date. Future documents are excluded.

Each observation needs:

* Unique `id`, `stock_id`, `field`, finite `value`.
* `period_start`, `period_end`, and `basis`: `single_quarter`, `ytd`, or `instant`.
* `scope=consolidated`, `unit=TWD`, `TWD/share` or `shares` as appropriate.
* `statement_type=income`, `cash_flow`, `eps_note`, or `balance`.
* `version_set`, `document_ids`, `locator`, `reviewed_by`, `values_reviewed=true`.
* `eps_kind=diluted` for `DilutedEPS`; `basic` for `BasicEPS`. They are separate slots.

Only whitelisted **period amounts** may be differenced. Current/previous YTD must
start January 1 of the same year and end at adjacent quarters, match company,
scope, unit, statement type and reviewed version set, and have exactly one explicit
bridge. A bridge contains `current_id`, `previous_id`, `field`, `version_set`,
`bridge_verified=true`, named reviewer, page locator and the exact union of source
`document_sha256s`. A matching version-set string alone is insufficient. Q1 YTD
period amounts are already one quarter. No cross-year subtraction is performed.

Annual/ytd EPS and weighted shares are never differenced. Point balances are never
converted into period flows. A missing Q4 direct diluted EPS stays unavailable.
Conflicting direct and derived quarter amounts are rejected rather than selecting
a preferred revision. `financial_period_audit` records rejected inputs and reasons.

## Calculations and correction editions

Gross/operating/consolidated net/parent net margins require matching approved
single-quarter numerator and revenue. EPS growth uses only compatible diluted
quarter disclosures; basic EPS is separately labeled. CFO/revenue and CFO/parent
profit require matched quarters/version sets. FCF additionally requires reviewed
cash capex; absence is not zero capex. These ratios are observations, not automatic
cross-industry quality rankings.

Every report exports the untouched `source-snapshot.json`, a recalculated
`snapshot.json`, `period-conversion.json` and the normal source/evidence package.
The HTML/PDF identifies corrected values; financial evidence cites the reviewed
originals rather than misattributing corrected values to FinMind. A correction
edition does not modify a sealed forecast, model inputs or original publication
time. Any model change requires its own new release; retrospective corrections
are not prospective performance.

Offline tests cover actual reviewed 2330/3017/3231 transcriptions, mock original
documents, periods, source hash/cutoff, version/statement/scope/unit mismatch,
basic/diluted separation, and forbidden EPS/share/balance differencing. Actual PDF
bytes and transcriptions are separately checked in cross-company acceptance.
