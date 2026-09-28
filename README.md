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

> Scaffolding stage — the pipeline is being built milestone by milestone (see `TODO.md`). The design target is:
> **clone → one command → query the published Parquet in DuckDB, with no cloud credentials.** This section will carry
> the real command once milestone 1 lands.

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
