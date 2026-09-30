# Querying clear-pricer

Three ways in, from zero setup to the full pipeline. All data is public (CMS hospital price files, the NPPES registry)
or synthetic (Synthea FHIR). **No PHI.**

## 1. No clone, no account: DuckDB over the published Parquet

Every data release is a set of Parquet files on the repo's GitHub Releases page. DuckDB reads them straight over HTTPS.

```bash
pip install duckdb
```

```python
import duckdb

R = "https://github.com/tjromack/clear-pricer/releases/latest/download"

# The headline: unresolved-NPI rate and disclosure coverage, per hospital
duckdb.sql(f"SELECT * FROM '{R}/rpt_npi_reconciliation.parquet'").show()

# One code across the three hospitals -- contracted dollars only (rate_basis = 'dollar')
duckdb.sql(f"""
    SELECT hospital_id, setting, charge_rows, rate_min, rate_median, rate_max
    FROM '{R}/agg_code_prices.parquet'
    WHERE code = '99213' AND rate_basis = 'dollar'
""").show()
```

The full charge-level fact (`fct_standard_charges.parquet`, 7.37M rows, ~117 MB) works the same way; DuckDB only
fetches the columns and row groups a query touches.

| File | What it is |
|---|---|
| `fct_standard_charges.parquet` | every charge row: hospital × item × setting × payer/plan, with `negotiated_rate` + `rate_basis` |
| `dim_charge_codes.parquet` | every billing code per item; declared type + `code_family` (join on `item_id`) |
| `agg_code_prices.parquet` | per hospital × code × setting × rate basis: row count, payer plans, min / p25 / median / p75 / max |
| `rpt_npi_reconciliation.parquet` | the headline NPI numbers; `rpt_npi_resolution` / `rpt_npi_completeness` are the evidence |
| `rpt_source_conformance.parquet` | what each hospital's file got wrong against the CMS v3 dictionary, and how often |
| `files.parquet` | the source files: SHA-256, template version, publish date, disclosed NPIs |
| `rpt_nppes_file_log.parquet` | every NPPES file applied by the CDC and what it changed |
| `rpt_fhir_summary` / `rpt_fhir_mapping` / `rpt_fhir_code_bridge` | the synthetic FHIR path: coverage, unmapped fields, code overlap |
| `manifest.json` | row counts, sizes, SHA-256 of every file, and the pinned inputs |

**Reading `negotiated_rate`:** it is the hospital's dollar figure whenever one is published, and `rate_basis` says
where it came from: `dollar` (contracted), `dollar_from_percent` (a percentage applied to the chargemaster price),
`dollar_percent_unreconciled` (a dollar and a percentage that disagree), and so on. Compare hospitals on
`rate_basis = 'dollar'` unless you mean to do otherwise. See `DECISIONS.md` CP-DEC 006.

## 2. Hosted REST: the served tables (Supabase)

The reports and the per-code price summary are served read-only over REST (Supabase). The key below is Supabase's
*publishable* key: public by design. The tables are protected by row-level security with a read-only policy; a write
with this key is refused (`permission denied`).

```bash
KEY=sb_publishable_uTjkQpUS8W5SgcMPKITQbw_oN3Pu4Qf

# the NPI reconciliation headline
curl "https://inznkisgrqutqcfzujwi.supabase.co/rest/v1/rpt_npi_reconciliation?order=hospital_id" -H "apikey: $KEY" -H "Accept-Profile: published"

# one code across the hospitals, contracted dollars only
curl "https://inznkisgrqutqcfzujwi.supabase.co/rest/v1/agg_code_prices?code=eq.99213&rate_basis=eq.dollar" -H "apikey: $KEY" -H "Accept-Profile: published"
```

Served tables: `agg_code_prices`, `rpt_npi_reconciliation`, `rpt_npi_resolution`, `rpt_npi_completeness`,
`rpt_source_conformance`, `files`, `rpt_nppes_file_log`, `dim_modifiers`, `rpt_fhir_summary`, `rpt_fhir_mapping`,
`rpt_fhir_code_bridge`. Filtering and paging follow PostgREST syntax (`?col=eq.value`, `&limit=`, `&offset=`); at most
1,000 rows per request. The full detail is in the Parquet release (section 1).

## 3. Clone and run it

```bash
git clone https://github.com/tjromack/clear-pricer && cd clear-pricer
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
clear-pricer run rush          # fetch -> parse -> stage -> dbt build with every gate (~20 s)
```

Then query `data/warehouse/clear_pricer.duckdb`, or run the read API over a release:

```bash
clear-pricer export                      # or download a release into data/published/
uvicorn clear_pricer.api:app             # http://127.0.0.1:8000/docs
```

`README.md` covers the rest of the pipeline (all three hospitals, NPPES, synthetic FHIR, the Airflow DAG).
