# EPS inventory and valuation basis audit

`tradingagents.research.valuation_audit` provides read-only checks. It does not
certify a fair multiple, change source records, or amend a published forecast.

* `quarter_window(latest_reported)` selects four quarters ending at the latest
  verified **earnings** period. Monthly revenue does not advance this date.
* `eps_inventory(stock_id, latest_reported, evidence, cutoff)` records each
  missing/rejected original. Only reviewed, source-bound, directly reported
  consolidated quarterly diluted EPS is eligible. Annual EPS, basic EPS,
  future captures/publications and duplicate unselected revisions cannot fill
  a missing quarter. A complete sum is not statutory annual weighted EPS and
  does not prove historical point-in-time availability.
* `vendor_history(records, cutoff)` computes current-revision descriptive
  statistics with an explicit excluded-row list and annual sample counts.
  Historical daily observations are not independent samples; a vendor median
  cannot become a forward fair multiple.
* `peer_basis(target, peer)` checks same price date, currency, consolidation
  scope and next-four-quarter diluted forecast horizon. A qualitative business
  peer still needs a separate business-mix, cash-conversion, growth-duration
  and capital-structure review. A passing basis is not fair-value validation.
* `single_quarter_reconciliation(vendor_values, reviewed_quarter)` compares
  revenue, operating profit and parent profit with a reviewed official quarter.
  A June 30 date alone does not establish Q2 basis: YTD amounts can coexist
  with single-quarter basic EPS. Do not calculate single-quarter margins or
  peer earnings from that mixed packet. This check rejects a mismatch; it does
  not automatically difference YTD values or alter the existing converter.

Source selection must verify the actual bytes/SHA256, company, period, currency,
scope, table column and EPS label before attesting to a reviewed row. These
checks consume that review; they do not extract accounting meaning from PDFs.

Official exchange PE may still reference an earlier financial-report quarter
than a new issuer earnings release. Preserve the exchange's financial-period
label and compare vendor and official PE on the exact same day. Numeric agreement
on selected dates establishes neither a diluted denominator nor historical
initial-version coverage. Listed TWSE responses cannot verify a TPEx issuer.

The 2026-10-25 audit found 3017 and 3231 each had three directly reported quarters
within 2025 Q3 through 2026 Q2; Q4 diluted EPS remained pending. The three selected
cross-year dates yielded 15 matching TWSE/vendor observations. Both findings are
current-capture diagnostics, not evidence of historical forecast performance.

Run offline checks using the established Taiwan test entry point:

```console
python -X utf8 -m unittest discover -s tests/taiwan -v
```
