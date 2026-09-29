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
- [x] `git init` + first commit + GitHub remote (`tjromack/clear-pricer`, private) + push

## Milestone 0 — confirm the three hospital files (don't pick blind) ✅ (2026-09-28)
- [x] Locate each hospital's CMS machine-readable file URL — found via each site's `cms-hpt.txt` (CP-DEC 005)
- [x] Fetch each via `curl` (proxy-safe); confirm it downloads and is parseable — all three parsed end to end (NM 5 GB stream-parsed)
- [x] Any that's missing/broken → swap — none needed; final three locked in CP-DEC 005
- [x] **Gate:** three confirmed, reachable, parseable source URLs. **← stop for approval**
- [x] First six real `docs/schema-drift-log.md` entries (BOM, scheme-less URL, header-less vendor endpoint, CPT/HCPCS typing, count-0 medians, dual rate encoding)

## Milestone 1 — one hospital, end to end ✅ (2026-09-29, Rush)
- [x] Landing → staging model for ONE hospital file (Rush, CP-DEC 007); the parser's **unmappable-field log** (`rpt_source_conformance`)
- [x] The three normalisation rules: rate precedence, zero-count medians nulled, `code_family` (CP-DEC 006)
- [x] dbt models + tests (row-count, uniqueness, not-null, enums, relationships + 5 singular gates) — tests **fail the run**; 3 broken fixtures each trip their gate (`tests/test_e2e_gates.py`)
- [x] DuckDB-local mode: clone → one command (`clear-pricer run rush`) → query `fct_standard_charges` (no cloud creds); staged Parquet byte-identical across re-runs
- [x] first `docs/schema-drift-log.md` entry (M0 wrote six; M1 closed their handling + added the truncation entry). **Gate:** one file clean end-to-end with a failing-capable gate. **stop**

## Milestone 2 — the Airflow DAG with failing gates (all three hospitals) ✅ (2026-09-29)
- [x] JSON parser (streaming, `ijson`) for UChicago + NM; BOM handling on the JSON path; item grain for codes (CP-DEC 008)
- [x] ETag conditional download (unchanged 5 GB NM file = one 304) + portable manifests
- [x] Airflow 3.3.2 DAG: stage ×3 → dbt build (gates) → publish; `@daily`; byte-identical restaging (tested)
- [x] Quality gates fail the DAG (not warn); run `broken_input_proof_1` went red at `dbt_build_gates` and publish was `upstream_failed`
- [x] Postgres (Docker) served parity with DuckDB-local: a gated publish plus a parity check that fails the task on mismatch. **Gate:** a red DAG on bad input. **stop**

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
