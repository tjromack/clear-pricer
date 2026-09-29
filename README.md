# clear-pricer

> © 2026 Trevor J. Romack — **MIT-licensed** ([LICENSE](LICENSE)) · tjromack@gmail.com

**A cleaned, versioned, queryable view of public hospital price and provider data — with the reconciliation failures
published rather than hidden.**

US hospitals are federally required to publish machine-readable price files, and the NPPES provider registry is public
— yet both are effectively unusable: the price files drift from their mandated schema, NPPES ships monthly full
replaces plus weekly deltas, and nobody reconciles the two. `clear-pricer` ingests them (plus synthetic FHIR R4 bundles
from Synthea for the clinical side), validates and reconciles them with quality gates that **fail the pipeline rather
than warn**, and republishes a clean, queryable dataset — publishing what it *couldn't* reconcile as a number, not
hiding it.

> **Public and synthetic data only — no PHI, no internal systems, no client data.** Where this touches a format that
> normally carries PHI (FHIR), it uses **synthetic** bundles by construction and says so.

**Demonstrates:** incremental pipelines with quality gates and schema-drift handling over messy public healthcare data —
with the referential-integrity failures (price-file NPI → NPPES) published as a rate, not swept under the rug.

---

## Who it's for

Anyone who hits the wall that mandated-but-unusable healthcare price data creates — journalists, researchers, patient
advocates, and self-insured employers benchmarking costs — plus data engineers who want a worked example of
schema-drift handling and referential-integrity reconciliation over real public data.

## What it does (v1 → v2)

- **v1 (in progress):** three Chicago hospitals — Northwestern Memorial, Rush, and University of Chicago Medical Center
  — through a scheduled pipeline with quality gates, published as queryable Parquet, runnable locally with no cloud
  credentials.
- **v2 (planned):** 50+ hospitals, NPPES reconciliation with a published unresolved-NPI rate, a public read API
  (FastAPI + Supabase), and a written analysis of price variation for the same CPT code across the Chicago metro.

## What this does *not* let you claim

- Not production-scale distributed processing.
- Not a complete or authoritative price index — a documented, reproducible slice with its reconciliation failures stated.
- No PHI, no real fee schedules beyond what hospitals publicly publish, no payer policy library.

## Quickstart

No cloud credentials. Needs Python 3.11+ and `curl` (on PATH by default on Windows 10+, macOS and Linux).

```bash
git clone https://github.com/tjromack/clear-pricer && cd clear-pricer
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

clear-pricer run rush      # discover (cms-hpt.txt) -> fetch (curl) -> parse -> stage -> dbt build + gates
```

The run exits non-zero if any quality gate fails. Then query the local DuckDB warehouse:

```bash
python -c "import duckdb; c = duckdb.connect('data/warehouse/clear_pricer.duckdb', read_only=True); \
print(c.sql('select rate_basis, count(*) from fct_standard_charges group by 1'))"
```

| Table | What it is |
|---|---|
| `fct_standard_charges` | one row per source charge row; `negotiated_rate` + `rate_basis` (where the dollar came from) |
| `dim_charge_codes` | every billing code, declared type kept verbatim + `code_family` derived from the code's shape |
| `rpt_source_conformance` | what each source file got wrong against the CMS v3 dictionary, and how often |
| `stg_hpt__files` | one row per source file: SHA-256, template version, Type-2 NPIs, row counts |

Offline, or to watch a gate fail: `clear-pricer run rush --source-file tests/fixtures/broken_ragged_rows.csv`.
v1 status: Rush is end to end (Milestone 1); UChicago and Northwestern land in Milestone 2.

## How it's verified (the differentiator)

Measured, not asserted — populated as milestones land:
- **Quality gates that fail the DAG** (not warn) — dbt tests for row-count, freshness, uniqueness, and mapping.
- **Referential integrity:** the price-file-NPI → NPPES unresolved rate, published as a number and explained.
- **A schema-drift log** (`docs/schema-drift-log.md`): what changed upstream, when, and how the pipeline handled it.

## Stack

Python · Airflow · dbt-core · DuckDB (local) / Postgres (Docker) / Supabase (served) · Docker · FastAPI · Synthea ·
GitHub Actions. See `DECISIONS.md` for why each choice was made.

## License

**MIT** — see [LICENSE](LICENSE). A tool over public data, meant to be run.
