# CLAUDE.md — Operating Contract

Working agreement for building **clear-pricer** with Claude Code. Read it before each session. The decisions this
contract assumes are recorded in `DECISIONS.md` (CP-DEC 001–004) — read those first; do not re-open them.

## Purpose

A cleaned, versioned, queryable view of public hospital price and provider data — with the reconciliation failures
published rather than hidden. Ingest CMS Hospital Price Transparency files + the NPPES provider registry + synthetic
Synthea FHIR R4 bundles; validate and reconcile them; republish as queryable Parquet + a public read API. The
interesting engineering is the messiness: price files drift from their mandated schema, NPPES ships monthly full
replaces plus weekly deltas, and every NPI in a price file should resolve to a provider in NPPES — the ones that don't
are the story.

## Design pins (do not violate without a DECISIONS entry)

1. **No PHI, by construction.** Every source is public or synthetic, and the README states this. Where the project
   touches a format that normally carries PHI (FHIR), it says explicitly that it does not.
2. **Idempotent by default.** Re-running any DAG day produces the same output. No truncate-and-reload where CDC is the
   honest model (NPPES).
3. **Schema drift is expected, not exceptional.** Every parser **records what it could not map** (an unmappable-field
   log) rather than dropping it silently. A source file that violates the expected schema is handled, logged, and
   surfaced — never silently skipped.
4. **dbt tests are part of the DAG,** not a separate manual step. Quality gates **fail the DAG**, they do not just warn.
5. **DuckDB local parity with the served Postgres is non-negotiable.** A stranger can run everything — clone, one
   command, query the published Parquet — **without cloud credentials.** This is the try-it path (Gate 6) and the
   clean-clone guarantee (Gate 1).
6. **Never widen scope to more hospitals before the quality gates are real.** v1 = 3 hospitals shipped with working
   gates, then v2 (CP-DEC 002). If the deadline slips, cut hospitals, not verification.
7. **Verification is part of the build (CP-DEC 003).** The unresolved-NPI rate is published as a number; the
   schema-drift log is written as it happens. If the check isn't written, the milestone isn't done.

## Environment gotcha (banked)

This machine runs a **TLS-inspecting proxy**: Python `requests`/`httpx` fail cert verification; `curl` (OS trust)
works. **All ingestion downloads go through `curl`, or inject `truststore.inject_into_ssl()` before any Python HTTPS.**

## Stack

- Python 3.11+
- **Airflow** (chosen over Dagster for résumé recognition — say exactly that if asked; it reads as judgment, not
  ignorance)
- **dbt-core**; **DuckDB** locally, **Postgres** (Docker) / **Supabase** served
- dbt tests (or Great Expectations) as the quality gates
- **Docker** (Airflow + Postgres), GitHub Actions
- **FastAPI** read layer
- **Synthea** for synthetic FHIR R4 bundles
- `pytest` for the pure parser/validation core

## Conventions

- **`data/` is gitignored in full** — raw dumps, DBs, and the published Parquet never enter git (Parquet ships via a
  GitHub Release). Small fixtures live in `tests/fixtures/`; the schema-drift log lives in `docs/` (committed).
- The **validation core is pure** — no I/O — so its tests are fast and total.
- No secrets in code; read from `.env` (gitignored).
- **Commit at each phase boundary** with a readable message; the git history is an interview artifact.
- Update `DECISIONS.md` on every non-trivial choice (landing-to-staging model, drift handling, reconciliation logic,
  the served schema) with the rejected alternative and the why.

## Definition of done (per milestone)

- The milestone's checklist in `TODO.md` is complete, and its **quality gate fails the DAG when it should**.
- The behaviour is demonstrable from a **clean clone** in DuckDB-local mode (no cloud creds).
- The verification artefact for that milestone is published (a passing/failing gate, an unresolved rate, a drift-log
  entry).
- New decisions recorded; a commit marks the boundary. **Stop and wait for approval before the next milestone.**

## Do not

- Do not commit raw data, DB files, or the published Parquet. Do not use real/PHI data.
- Do not let a gate merely warn — a real gate fails the run.
- Do not scale hospitals before v1's gates are real.
- Do not ship a number you can't reproduce on request (portfolio hard rule).

## Case-study voice

State plainly what the system is, what it does, the decisions made, and what was learned.
- No disclaimers about the author's experience. Limits belong to the system, stated as scope or cost.
- No honesty-signalling ("the honest version"). State the number.
- Real limits, costs, and failures stay — as facts about the system, not confessions. The **schema-drift log** and the
  **unresolved-NPI rate** are the credibility, not an embarrassment.
