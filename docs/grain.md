# Grain of the published release

What one row of each release table means, and the key that identifies it. Every key here is **proved, not assumed**:
it is a dbt gate (`grain`, in the schema files), so a release with a duplicate key is never cut. The same keys drive
`check_values.json`, which ships with every release (CP-DEC 020), and `clear-pricer verify-release`, which
recomputes it from a clean download.

Figures quoted below come from release `‹TAG›` and its `check_values.json`. Each release carries its own; the
structure described here does not change between releases.

```bash
clear-pricer verify-release --tag latest     # hashes, grain, fan-out, checksums, history invariants: PASS or FAIL
```

## Summary

| File | One row per | Key | Rows (`‹TAG›`) |
|---|---|---|---:|
| `fct_standard_charges` | source charge row (item × setting × payer/plan, as the hospital published it) | `charge_id` | 7,371,416 |
| `dim_charge_codes` | billing code listed on an item, in source order | `item_id`, `code_seq` | 1,092,359 |
| `agg_code_prices` | hospital × code family × code × setting × rate basis | `hospital_id`, `code_family`, `code`, `setting`, `rate_basis` | 49,404 |
| `rpt_npi_reconciliation` | hospital, plus one `ALL` row | `hospital_id` | 4 |
| `rpt_npi_resolution` | hospital × NPI the hospital disclosed | `hospital_id`, `npi` | 8 |
| `rpt_npi_completeness` | hospital × NPPES hospital NPI near it (tiers 1–4) | `hospital_id`, `npi` | 78 |
| `rpt_source_conformance` | hospital × kind of deviation × column | `hospital_id`, `kind`, `column` | 9 |
| `files` | hospital (its current source file) | `hospital_id` | 3 |
| `rpt_nppes_file_log` | NPPES file the CDC applied or refused, in order | `seq` | ‹N› |
| `dim_modifiers` | modifier rule a hospital published | `hospital_id`, `code`, `setting`, `payer_name`, `plan_name` | 37 |
| `rpt_fhir_summary` | FHIR resource type | `resource_type` | ‹N› |
| `rpt_fhir_mapping` | FHIR resource type × leaf path | `resource_type`, `path` | ‹N› |
| `rpt_fhir_code_bridge` | code system on synthetic claim lines | `code_system` | ‹N› |
| `rpt_fhir_claim_totals` | synthetic claim type | `claim_type` | 3 |
| `dim_provider_history` | NPI × version (SCD type 2) | `npi`, `version` | ‹N› |

## The joins that change the grain

**Charges → codes fans out ‹FANOUT›×.** `dim_charge_codes` is one-to-many from an item: an item lists a CPT code, a
revenue code, a chargemaster number and so on, and every charge row of the item carries them all. Joining
`fct_standard_charges` to `dim_charge_codes` on `item_id` turns 7,371,416 charge rows into ‹XCODES› rows
(`derived.charge_x_code_rows`). `count(*)` or `sum(negotiated_rate)` after that join counts each charge once per code.
Filter the codes to one family first, or count `distinct charge_id`.

One item can even list the same code string twice under different declared types (148 rows in `‹TAG›`), so
`(item_id, code)` is not a key; `(item_id, code_seq)` is.

**`agg_code_prices` is finer than hospital + code.** Its 49,404 rows cover ‹PAIRS› hospital-code pairs,
‹ROWSPERPAIR› rows per pair (`derived.agg_code_prices_rows_per_hospital_code`), because one code is priced separately
per setting (inpatient / outpatient / both / unspecified) and per `rate_basis` (contracted dollar, dollar derived from
a percentage, …). Joining it to anything at hospital-plus-code grain, or averaging its medians per code, mixes those
rows and counts each code more than once. Pick one `setting` and one `rate_basis` (usually `'dollar'`) before
comparing hospitals; never add or average `rate_median` across rows.

`charge_rows` does add up: `sum(charge_rows)` equals the number of distinct charges whose item carries a qualifying
code (`derived.agg_code_prices_fanout` = ‹AGGFANOUT›), because no charge in this release carries two qualifying codes.
If one ever does, that ratio rises above 1.0 in `check_values.json`. The gate `assert_agg_code_prices_match_lines`
re-derives every row of this table from the charge lines, in both directions: header rows without lines, lines without
a header row, a count off by one, or a rate off by more than half a cent all fail the run.

**`rpt_npi_reconciliation` carries its own total.** The `ALL` row is the sum of the hospital rows. Summing the
whole table counts every NPI twice (16 disclosed NPIs instead of 8). Filter `hospital_id = 'ALL'`, or exclude it.

**NULL is part of the `dim_modifiers` key.** A modifier published at file level has no setting, payer or plan, so
those key columns are NULL on every row of `‹TAG›`. The grain gate groups NULLs together, so two file-level rules for
the same code would still fail it. In SQL, `col = NULL` matches nothing: join on `IS NOT DISTINCT FROM`.

## `dim_provider_history`: the NPPES change history

One row per NPI per version, ‹HIST› rows over ‹NPIS› NPIs. All entity types are kept: `1` individual, `2`
organization, and NULL for NPIs first seen as a deactivation notice, which NPPES publishes with every field but the
NPI and date blank. NPPES is a public registry; CMS publishes all of it.

**Intervals are half-open.** A version is valid on day `d` when `valid_from <= d < valid_to`. `valid_to` is NULL on
the current version, and exactly one version per NPI is current. As of a date:

```sql
SELECT * FROM dim_provider_history
WHERE npi = '1497859649'                                 -- a Northwestern NPI
  AND valid_from <= DATE '2026-06-30' AND (valid_to IS NULL OR DATE '2026-06-30' < valid_to);
```

**Two gates prove the history.** Each closed version ends on the day the next one starts, and no two versions of
an NPI are valid on the same day (`assert_provider_history_no_overlap`, plus `derived.provider_history_*` in
`check_values.json`). Anyone can rerun the second check over HTTPS:

```sql
SELECT count(*) AS versions_valid_on_one_day
FROM 'https://github.com/tjromack/clear-pricer/releases/latest/download/dim_provider_history.parquet' a
JOIN 'https://github.com/tjromack/clear-pricer/releases/latest/download/dim_provider_history.parquet' b
  ON a.npi = b.npi AND a.version < b.version
 AND a.valid_from < coalesce(b.valid_to, DATE '9999-12-31')
 AND b.valid_from < coalesce(a.valid_to, DATE '9999-12-31');   -- 0
```

‹ZEROLEN› version is zero-length (`valid_from = valid_to`): NPI `1801771704`, inserted and updated on the same day. The NPPES
full file overlaps the next weekly file by a day (drift log, 2026-09-29). A zero-length version is valid on no day,
so it never answers an as-of query.

`valid_from` is the record's own effective date (latest of Last Update, Deactivation and Reactivation date), not
the day this pipeline saw it (CP-DEC 010). The history begins with the first full file applied
(`rpt_nppes_file_log`). On the hosted runner it is carried between runs in the Actions cache. If the cache is evicted,
the history is rebuilt from the files CMS still lists, and the next release changes visibly.

## The synthetic claims (`rpt_fhir_claim_totals`)

One row per claim type, measuring header against lines: `Claim.total` against the sum of `item.net`, in integer
cents. Synthea's header and lines do not agree (drift log, 2026-10-07):
- **Pharmacy:** claims carry no `net` on their single line.
- **Professional and institutional:** the header sits above the lines on some claims and below on others.

This table publishes that as a source property. The parts this pipeline owns are gated (`assert_fhir_claims_match_eobs`):
- every line has a claim, and every claim has lines;
- every claim has exactly one EOB;
- every EOB's total equals its claim's total within half a cent.

## Money

Money columns are DOUBLE in the Parquet. `check_values.json` checksums them as integer cents
(`sum(round(x * 100))`), because a floating-point sum depends on the order it is added in. The gates compare money
with a half-cent tolerance, never exactly.
