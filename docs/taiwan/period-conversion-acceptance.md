# Quarterly period correction acceptance

This correction depends on PR #10 (four-quarter conditional earnings and report
publication). Its tested parent file tree is
`01ce1075ee077d67d5ef68024d830ae788fd95a4`; remote PR #10 head is
`313ec5a7f1df0b4443a6a3b5faa1e1928ca0b877`. Local and remote commit histories differ,
but their parent file trees match. Merge #10 before this correction.

## Reproducible checks

On 2026-10-26 the correction at local commit
`3d1a7864cb6dc36241ce001c85ff1522fe732e78` passed:

- `python -m unittest discover -s tests/taiwan -q`: 340 tests, zero failures.
- `ruff check .`: passed across the repository.
- Three source-bound company integrations, preserving original vendor values.
- 36 future quarter/scenario EPS comparisons: no numerical changes.
- 14 protected prior forecast/report files: identical SHA256, including the
  original releases and their publication times.

The unit-test originals are deliberately synthetic. Separate integration used
the archived official PDF bytes, reviewed columns, source hashes and version
bridges. This is current-version research acceptance, not strict historical PIT
performance or investment accuracy.

## Reviewed Q2 2026 results

| Company | Revenue (TWD) | Gross margin | Operating margin | Diluted EPS | CFO (TWD) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2330 | 1,270,380,250,000 | 67.720723% | 60.344346% | 27.25 | 783,364,977,000 |
| 3017 | 49,120,964,000 | 32.570000% | 27.437611% | 23.92 | 12,616,941,000 |
| 3231 | 895,442,616,000 | 5.657943% | 3.735279% | 4.36 | -110,750,946,000 |

3017 revenue: 98,158,910,000 H1 less 49,037,946,000 Q1. 3231 revenue:
1,741,745,642,000 H1 less 846,303,026,000 Q1. Only reviewed compatible amount
bridges were used. EPS and weighted shares were never differenced. Basic EPS is
kept separately; vendor basic EPS is not relabeled diluted.

Unknown periods, incompatible versions, future sources, monthly sums mislabeled
as quarterly statements, and mixed basic/diluted EPS cannot enable quarterly
calculations. An older reviewed quarter does not certify a newer unknown one.

## Report and archive checks

Correction HTML links and all rendered PDF pages were checked: 2330 seven pages,
3017 seven pages, 3231 eight pages. The portable evidence archive contains 363
manifest-verified files. The correction is a new edition, not a forecast reissue.

Original sealed releases retained:

| Company | Actual original publication (UTC) | Raw file SHA256 |
| --- | --- | --- |
| 3017 | 2026-10-15T08:37:02.215913+00:00 | c57bffa6a3360ad7a19cf8a55573fc217778e74cd3df63256331d553e05a5289 |
| 3231 | 2026-10-15T11:25:47.223385+00:00 | 2bec58e56f303e2e0bc0efbdddb9f683ab6b319b09d06ccd1141ca224a02ee2a |

## Merge acceptance still to run

Run the existing Sponsor workflow on merged `main`, then use that exact main
revision for credential-free official-review conversion and HTML/PDF generation.
The Sponsor job itself does not render PDFs and does not automatically certify
the accounting columns of newly downloaded documents. Its unreviewed financial
fields must remain unavailable until the source-review ledger is supplied.

3017/3231 direct Q4 diluted EPS and independently supported fair valuation
multiples remain separate gaps. Seven historical event/version mappings remain
pending; this correction does not prove original historical vintages.
