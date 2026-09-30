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

## Milestone 3 — NPPES incremental load + CDC ✅ (2026-09-29)
- [x] NPPES full + weekly delta ingestion; change-data-capture into an SCD2 history (not truncate-and-reload) — CP-DEC 010
- [x] Stream-and-project from the zip (11.7 GB CSV → 9.8M providers in ~1 min); full header drift check
- [x] Record-date ordering + file-coverage tie-break; deactivation stubs carry identity forward; absent-from-full tombstones
- [x] dbt CDC gates (one current version, contiguous versions, valid intervals, no rejected files, NPI format)
- [x] `nppes_sync` task in the Airflow DAG; NPPES file log published to Postgres with parity
- [x] **Gate:** re-applying every file leaves the 9,855,257-row history identical (fingerprinted); weekly deltas
  touch only their ~34k records. **stop**

## Milestone 4 — NPI reconciliation (the story) ✅ (2026-09-29)
- [x] Resolve every price-file NPI against NPPES (check digit, status, as-of status, type, taxonomy, name, address)
- [x] Completeness direction: undisclosed hospital NPIs in NPPES as tiered, evidence-backed candidates (CP-DEC 011)
- [x] **Publish the unresolved rate as a number** + explain it: 0.0% unresolved, 13.3% disclosure coverage, with
  threshold sensitivity → `docs/results/npi-reconciliation.md` (`clear-pricer report`, byte-reproducible)
- [x] Gates: resolution completeness, candidate-rule adherence, headline consistency, macro unit checks; published
  to Postgres with parity
- [x] Warehouse made self-contained (no views over absolute paths)
- [x] **Gate:** the unresolved-NPI rate is a committed, reproducible figure. **stop**

## Milestone 5 — Synthea FHIR ingestion path ✅ (2026-09-29)
- [x] Pinned, containerised, byte-deterministic Synthea v4.0.0 population (225 patients, 240,237 resources) — CP-DEC 012
- [x] Parse/map/validate synthetic FHIR R4 bundles: 11 resource types modelled; structural + referential validation
  (1,200,521 of 1,200,521 references resolve, 0 structural issues)
- [x] Mapping report measured by read-tracking → `docs/results/fhir-mapping.md`; code bridge to price files (CDT only)
- [x] **No-PHI gate:** every patient must carry Synthea's markers; a non-synthetic patient turns the run red (tested)
- [x] `stage_fhir` in the DAG; FHIR reports published to Postgres with parity; clean-clone path still green
- [x] **Gate:** resources parsed + validated with a mapping report. **stop**

## Milestone 6 — publish + serve ✅ (2026-09-29)
- [x] Published Parquet release (GitHub Release, not committed): 13 files, byte-deterministic, manifest pins inputs +
  output hashes; a release is cut only when the fingerprint changes — CP-DEC 013
- [x] Query docs for strangers (`docs/QUERY.md`): DuckDB over HTTPS, hosted REST, clone-and-run
- [x] Supabase (free tier) served set: 11 tables incl. the 49,404-row per-code price summary; RLS + read-only policy
  + grants re-applied inside every swap; anon writes refused (tested over the live REST API)
- [x] FastAPI read layer over the release Parquet (no DB, no credentials)
- [x] Credential hygiene in code: DSN passwords redacted from every driver error (after a real leak — see BUILD-LOG)
- [x] Hosted schedule (CP-DEC 009): `pipeline.yml` runs stage → build (gates) → publish → export → release daily
- [x] CI clean-clone check (`ci.yml`) green on a fresh runner; badges in README
- [x] Repo public
- [x] **Gate:** a stranger queries the release with no clone and no credentials. **stop**
## Milestone 7 — the analysis (v2 shareable proof) ✅ (2026-09-29)
- [x] Price variation for the same CPT code across the three Chicago hospitals — `docs/analysis/price-variation.md`
  (list 2.11× median across 2,323 codes; cash 3.38×; contracted Rush vs UChicago 1.55× vs within-UChicago payer spread 4.06×)
- [x] Comparable-price method decided before writing, after finding `rate_basis` mixes unlike dollars (CP-DEC 015)
- [x] Every number computed from a pinned public release (`clear-pricer analysis --release …`); three figures (light +
  dark, palette validated); results JSON; tests enforce the method and byte-reproducibility
- [x] **Gate:** a reproducible, shareable analysis with its method + caveats stated. **stop**

## Milestone 8 — case study + README results ✅ (2026-09-29)
- [x] `docs/CASE-STUDY.md` (schema-drift log is the spine) + README "How it's verified" with the real numbers
- [x] Clean-clone check (GitHub Action) so Gate 1 is a badge, not a claim — landed in M6 (`ci.yml`)
- [x] Voice pass over public docs (CLAUDE.md case-study rules); every case-study number checked against its source
- [x] v1/v2 scope recorded (CP-DEC 016): v1 shipped with v2's features at three-hospital scale; v2 = scale-out
- [x] **stop**

## Later / maybe
- [ ] v2 scale-out: 50+ hospitals through the same gates (CP-DEC 016); more metros
- [ ] publicly hosted API (FastAPI) — a hosting decision
- [ ] FHIR ExplanationOfBenefit adjudication (payment detail; 5.3% mapped today)
- [ ] NPPES secondary practice locations (widens NPI completeness matching)
- [ ] per-hospital "comparable-price share" tracked across releases
- [ ] confirm the Supabase free-tier project stays active under the daily run (still active after night 1)
- [x] schedule resilience after night 1's dropped run: second daily slot + freshness watchdog (CP-DEC 017)
- [ ] observe a week of unattended runs; confirm the watchdog stays green on healthy days
- [ ] alerting beyond GitHub's failed-run email (e.g. a webhook), if one inbox proves too easy to miss
