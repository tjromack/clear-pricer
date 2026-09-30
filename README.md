# clear-pricer

[![ci](https://github.com/tjromack/clear-pricer/actions/workflows/ci.yml/badge.svg)](https://github.com/tjromack/clear-pricer/actions/workflows/ci.yml) [![pipeline](https://github.com/tjromack/clear-pricer/actions/workflows/pipeline.yml/badge.svg)](https://github.com/tjromack/clear-pricer/actions/workflows/pipeline.yml)

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

**Demonstrates:** incremental pipelines with quality gates and schema-drift handling over messy public healthcare data,
with the referential-integrity results (price-file NPI ↔ NPPES) published as rates.

**Case study: [docs/CASE-STUDY.md](docs/CASE-STUDY.md)**: what the sources actually contained, the decisions it forced,
and what broke along the way.

---

## Who it's for

Anyone who hits the wall that mandated-but-unusable healthcare price data creates — journalists, researchers, patient
advocates, and self-insured employers benchmarking costs — plus data engineers who want a worked example of
schema-drift handling and referential-integrity reconciliation over real public data.

## What it does (v1 → v2)

- **v1 (shipped 2026-09-29):** three Chicago hospitals (Northwestern Memorial, Rush, and University of Chicago
  Medical Center; 7.37M charge rows) through a gated pipeline, rebuilt daily by a hosted GitHub Actions run. It
  includes NPPES change-data-capture, two-directional NPI reconciliation, a synthetic FHIR R4 path, a byte-reproducible
  Parquet release, a read-only served tier and API, and a price-variation analysis.
- **v2 (planned):** scale-out to 50+ hospitals, where the NPI rates become statistically meaningful and a
  same-code comparison can speak for the metro; a publicly hosted API. See `DECISIONS.md` CP-DEC 016.

## What this does *not* let you claim

- Not production-scale distributed processing.
- Not a complete or authoritative price index — a documented, reproducible slice with its reconciliation failures stated.
- No PHI, no real fee schedules beyond what hospitals publicly publish, no payer policy library.

## Read the analysis

**[Same code, different price: three Chicago hospitals](docs/analysis/price-variation.md).** List prices for the same outpatient service differ a median 2.1× across Northwestern, Rush and UChicago. For insured patients, *who pays* moves the price more than *where they go*: at UChicago the same service varies a median 4.1× across its payers, against a 1.55× typical gap between Rush and UChicago. Every number is regenerated from a public data release by one command.

## Query it without cloning

Every data release is Parquet on the [Releases page](https://github.com/tjromack/clear-pricer/releases), readable by DuckDB straight over HTTPS; the reports and a per-code price summary are also served read-only over REST. See **[docs/QUERY.md](docs/QUERY.md)**.

```python
import duckdb
duckdb.sql("SELECT * FROM 'https://github.com/tjromack/clear-pricer/releases/latest/download/rpt_npi_reconciliation.parquet'").show()
```

## Quickstart (clone and run)

No cloud credentials. Needs Python 3.11+ and `curl` (on PATH by default on Windows 10+, macOS and Linux).

```bash
git clone https://github.com/tjromack/clear-pricer && cd clear-pricer
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

clear-pricer run rush      # discover (cms-hpt.txt) -> fetch (curl) -> parse -> stage -> dbt build + gates
clear-pricer run uchicago  # 42 MB JSON
clear-pricer run nm        # Northwestern: a 5 GB JSON download, ~3 min to stream-parse (skip it to stay light)
clear-pricer nppes-sync    # NPPES registry: 1.2 GB monthly + weekly deltas -> CDC history (~6 min first time)
clear-pricer synthea-generate  # synthetic FHIR R4 population (Synthea in Docker; ~25 s) -- no PHI
clear-pricer fhir-stage        # parse + validate it
clear-pricer build         # re-run the dbt gates over everything loaded
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
| `dim_modifiers` | payer-specific modifier rules published at file level (JSON sources) |
| `stg_hpt__files` | one row per source file: SHA-256, template version, Type-2 NPIs, row counts |
| `dim_providers_current` | the NPPES registry as of the latest applied file, one row per NPI (deactivations included) |
| `stg_nppes__provider_history` | NPPES SCD2 history: every version of every provider, with `valid_from`/`valid_to` |
| `rpt_nppes_file_log` | every NPPES file applied, and what it changed (inserted / updated / deactivated / stale) |
| `rpt_npi_reconciliation` | the headline: unresolved-NPI rate + disclosure coverage, per hospital and overall |
| `rpt_npi_resolution` / `rpt_npi_completeness` | the evidence behind it, NPI by NPI |
| `rpt_fhir_summary` / `rpt_fhir_mapping` | the synthetic FHIR path: what was parsed, what was mapped (measured), what wasn't |
| `fct_fhir_claim_lines` / `rpt_fhir_code_bridge` | synthetic claim lines, and how many share a code system with real price files |

Offline, or to watch a gate fail: `clear-pricer run rush --source-file tests/fixtures/broken_ragged_rows.csv`.

### Scheduled + served (optional: Docker)

```bash
docker compose up -d       # Postgres 16 (localhost:5433) + Airflow 3 (http://localhost:8081, local dev: no login)
```

The `clear_pricer_hpt` DAG runs daily: `stage_{rush,uchicago,nm}` + `nppes_sync` → `dbt_build_gates` →
`publish_postgres`.
A failing gate turns the run red and **publish never runs**, so the served `published` schema always holds the last
gated build, and a parity check proves it matches DuckDB. To watch the gate fire, trigger the DAG with
`{"source_overrides": {"rush": "tests/fixtures/broken_ragged_rows.csv"}}`. The DuckDB path above needs none of this.

## How it's verified

Measured, not asserted (as of 2026-09-29; every figure regenerates from pinned inputs):
- **Reproducible across machines:** a GitHub runner (Linux) and a workstation (Windows) independently built the same
  release, fingerprint `27a340045c24de0f…`, with all 13 Parquet files byte-identical. A fresh container with no clone
  and no credentials queried it over HTTPS, including the full 7.37M-row fact.
- **Quality gates that fail the run** (not warn): 80 dbt checks, covering row reconciliation, keys, enums, CMS
  required columns, the CDC history's invariants and the reconciliation's own rules. A deliberately broken input turns
  the Airflow run red, and publish never runs (run `broken_input_proof_1`).
- **NPI reconciliation, both directions** ([docs/results/npi-reconciliation.md](docs/results/npi-reconciliation.md)):
  **unresolved-NPI rate 0.0%** (8 of 8 disclosed Type 2 NPIs resolve, all verified). **Disclosure coverage 13.3%**:
  NPPES holds 52 more active hospital NPIs registered under the same hospital names at the same addresses or campus
  ZIPs (46–65 across the threshold sensitivity band), published as evidence-backed candidates.
- **Publisher conformance, measured:** e.g. 92.8% of Northwestern's payer rows report a median over zero claims;
  55.9% of its dollar-plus-percentage rates don't reconcile. See `rpt_source_conformance`.
- **A schema-drift log** ([docs/schema-drift-log.md](docs/schema-drift-log.md)): 21 upstream deviations, each with
  when it was found and how the pipeline handles it (record, never drop).
- **Parity:** every publish checks counts, distinct keys and money sums between DuckDB and the served Postgres, and
  a mismatch fails the run.
- **Price variation** ([docs/analysis/price-variation.md](docs/analysis/price-variation.md)): every number computed
  from a pinned release by one command; tests enforce its method.
- **Synthetic FHIR, validated** ([docs/results/fhir-mapping.md](docs/results/fhir-mapping.md)): 240,237 Synthea
  R4 resources; every one of 1,200,521 references resolves; a gate fails the run if any patient lacks Synthea's
  synthetic markers (no PHI, by construction and by test).
- **Idempotency, proven:** restaging a file yields byte-identical Parquet; re-applying every NPPES file leaves the
  9.86M-row history identical.

## Stack

Python · DuckDB · dbt-core · Airflow 3 · Postgres (Docker) / Supabase (served) · FastAPI · pyarrow / ijson · Synthea ·
Docker · GitHub Actions. See `DECISIONS.md` (CP-DEC 001–016) for why each choice was made.

## License

**MIT** — see [LICENSE](LICENSE). A tool over public data, meant to be run.
