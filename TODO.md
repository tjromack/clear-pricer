# TODO — the build spine

Phased milestones, each ending at a **quality gate + a commit + a stop**. The order is the kickoff-packet spine: one
hospital end-to-end before scaling, verification baked into every step, DuckDB-local parity from early so it's always
demoable. v1/v2 split (CP-DEC 002) is the guardrail against sprawl — **cut hospitals, not verification.**

Decisions already settled (do not re-open): `DECISIONS.md` CP-DEC 001–004.

---

## Phase 0 — scaffold ✅ (this session)
- [x] `DECISIONS.md` (scope, Chicago, 3 hospitals, v1/v2, Postgres+Supabase, MIT, proxy gotcha)
- [x] `CLAUDE.md` — the design pins + the proxy gotcha + verification-first discipline
- [x] `README.md` — the promise + who-for + does-not-do + v1/v2 + how-it's-verified stub
- [x] `TODO.md` — this spine
- [x] `.gitignore` — **`data/` ignored in full** + DB/dbt/airflow artefacts + secrets
- [x] `LICENSE` (MIT) · `data/README.md` (data is gitignored; how to fetch)
- [ ] `git init` + first commit + GitHub remote (`tjromack/clear-pricer`, private) + push

## Milestone 0 — confirm the three hospital files (don't pick blind)
- [ ] Locate each hospital's CMS machine-readable file URL (standard-charges page; EIN-based filename)
- [ ] Fetch each via `curl` (proxy-safe); confirm it downloads and is parseable (JSON/CSV, size, top-level shape)
- [ ] Any that's missing/broken → swap for another Chicago system; record the final three in `DECISIONS.md`
- [ ] **Gate:** three confirmed, reachable, parseable source URLs. **← stop for approval**

## Milestone 1 — one hospital, end to end
- [ ] Landing → staging model for ONE hospital file; the parser's **unmappable-field log**
- [ ] dbt models + tests (row-count, uniqueness, not-null) — tests **fail the DAG**
- [ ] DuckDB-local mode: clone → one command → query the staged table (no cloud creds)
- [ ] first `docs/schema-drift-log.md` entry. **Gate:** one file clean end-to-end with a failing-capable gate. **stop**

## Milestone 2 — the Airflow DAG with failing gates (all three hospitals)
- [ ] Airflow DAG: fetch → parse → stage → dbt test, scheduled, idempotent
- [ ] Quality gates fail the DAG (not warn); a deliberately-broken input proves the gate fires
- [ ] Postgres (Docker) served parity with DuckDB-local. **Gate:** a red DAG on bad input. **stop**

## Milestone 3 — NPPES incremental load + CDC
- [ ] NPPES full replace + weekly delta ingestion; change-data-capture (not truncate-and-reload)
- [ ] **Gate:** a re-run is idempotent; a delta updates without a full reload. **stop**

## Milestone 4 — NPI reconciliation (the story)
- [ ] Resolve every price-file NPI against NPPES; **publish the unresolved rate as a number** + explain it
- [ ] **Gate:** the unresolved-NPI rate is a committed, reproducible figure. **stop**

## Milestone 5 — Synthea FHIR ingestion path
- [ ] Parse/map/validate synthetic FHIR R4 bundles (the clinical side; FHIR on the résumé, honestly, no PHI)
- [ ] **Gate:** resources parsed + validated with a mapping report. **stop**

## Milestone 6 — publish + serve
- [ ] Published Parquet release (GitHub Release, not committed); DuckDB-local query docs
- [ ] FastAPI read layer → Supabase (v2). **Gate:** a stranger clones + queries with no cloud creds. **stop**

## Milestone 7 — the analysis (v2 shareable proof)
- [ ] Price variation for the same CPT code across the Chicago metro — one written analysis, published
- [ ] **Gate:** a reproducible, shareable analysis with its method + caveats stated. **stop**

## Milestone 8 — case study + README results
- [ ] `docs/CASE-STUDY.md` (schema-drift log is the spine) + README "How it's verified" with the real numbers
- [ ] Clean-clone check (GitHub Action) so Gate 1 is a badge, not a claim. **stop**

## Later / maybe
- [ ] more metros; a hosted read API; a second CPT-comparison analysis
