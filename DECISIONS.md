# clear-pricer — decisions log

> Append-only. Point-in-time record of the calls made, so they are not re-litigated. Author: Trevor J. Romack.
> This is BUILD 2 of the portfolio slate — a public healthcare price & provider data platform.

## CP-DEC 001 — Scope, home, and stack (2026-09-28)
**Status:** Decided.

- **What:** ingest, validate, reconcile and republish healthcare data that is legally public and practically unusable —
  CMS Hospital Price Transparency machine-readable files + the NPPES provider registry + synthetic Synthea FHIR R4
  bundles. The referential-integrity spine: every NPI in a price file should resolve to a provider in NPPES; the ones
  that don't are the story.
- **Home:** its own repo at `C:/dev/clear-pricer`, own GitHub remote `tjromack/clear-pricer` (private until ready).
  Deliberately **outside** the `c:/ai` AI/LLM workspace — this is the data-engineering pillar, with its own infra and
  large data footprint.
- **Metro / v1 targets:** **Chicago.** Three distinct systems (so a same-CPT metro comparison is meaningful):
  **Northwestern Memorial Hospital**, **Rush University Medical Center**, **University of Chicago Medical Center**.
  *Candidates, not blind picks:* milestone 0 confirms each publishes a reachable, parseable machine-readable file and
  swaps any that don't (CMS mandates them; file name is EIN-based, listed on each hospital's "standard charges" page).
- **Served store:** Postgres-in-Docker locally; **Supabase** (free tier) for the public read API in v2.
- **License:** MIT — this is a *tool* with genuine public value (price transparency data is mandated and effectively
  unusable), meant to be run, not a product to protect.

## CP-DEC 002 — The v1/v2 split is the guardrail against sprawl (2026-09-28)
**Status:** Decided (the playbook's #1 named failure mode for this build is infinite-hospital sprawl).

- **v1:** three Chicago hospitals, one metro, a scheduled pipeline, quality gates that FAIL the DAG, published Parquet,
  an honest README. Ship this before touching v2.
- **v2:** 50+ hospitals, NPPES reconciliation with a published unresolved rate, a public read API, and one written
  analysis — price variation for the same CPT code across the Chicago metro (the shareable analyst proof).
- **The rule:** if the v1 deadline arrives and it isn't shipped with working gates, **cut hospitals, not verification.**

## CP-DEC 003 — Verification is part of the build, not the write-up (2026-09-28)
**Status:** Decided (the portfolio's one overriding discipline).

Every milestone ships its check: dbt tests that *fail the DAG* (not warn), the unresolved-NPI rate as a published
number, and the **schema-drift log** (`docs/schema-drift-log.md`) — what changed upstream, when, and how the pipeline
handled it. This log is the spine of the case study and is near-impossible to reconstruct afterwards, so it is written
as it happens. If the check isn't written, the milestone isn't done.

## CP-DEC 004 — Banked environment gotcha: the TLS proxy (2026-09-28)
**Status:** Decided.

This machine runs a TLS-inspecting proxy. Outbound HTTPS from Python `requests`/`httpx` fails cert verification; `curl`
(OS trust store) works. **All ingestion downloads (CMS, NPPES) go through `curl` or with `truststore.inject_into_ssl()`
injected first.** Bank this in CLAUDE.md so ingestion doesn't stall on day one.

## CP-DEC 005 — The v1 hospitals are locked, and sources are discovered via `cms-hpt.txt` (2026-09-28)
**Status:** Decided (Milestone 0 gate).

All three candidates from CP-DEC 001 were confirmed: reachable, downloaded in full with `curl`, and parsed end to end.
None needed swapping.

| Hospital | EIN | Format · CMS template | Size | `last_updated_on` | Parsed | Type-2 NPIs in header |
|---|---|---|---|---|---|---|
| Northwestern Memorial Hospital | 370960170 | JSON · v3.0.0 | 5,019,534,101 B | 2026-04-01 | 118,403 items · 7,026,150 payer rows | 2 |
| Rush University Medical Center | 362174823 | CSV (tall) · v3.0.0 | 69,013,318 B | 2026-09-25 | 208,409 rows · 0 ragged | 1 |
| University of Chicago Medical Center | 363488183 | JSON · v3.0.0 (UTF-8 BOM) | 42,386,565 B | 2026-04-01 | 49,438 items · 100,333 payer rows | 5 |

MRF URLs, as published in each hospital's `cms-hpt.txt`:
- NM: `https://www.nm.org/site_data/370960170_northwestern-memorial-hospital_standardcharges.json`
- Rush: `https://apps.para-hcfs.com/PTT/FinalLinks/Reports.aspx?dbName=dbRUMCCHICAGOIL&type=CDMWithoutLabel&fileType=CSV`
  (the scheme is missing in the source; the file name `362174823_rush-university-medical-center_standardcharges.csv`
  arrives via `Content-Disposition`)
- UChicago: `https://edge.sitecorecloud.io/unichicagomc-81nbqnb3/media/files/pricing-transparency/2026/363488183_the-university-of-chicago-medical-center_standardcharges.json`

M0 SHA-256 values (2026-09-28): NM `908ac958…3712`, Rush `47f31395…3b80`, UChicago `a0f699c3…6ac7`.

- **Discovery goes through `cms-hpt.txt`, not hard-coded URLs.** CMS requires each hospital to publish
  `/cms-hpt.txt` at its site root, listing `location-name`, `source-page-url` and `mrf-url`. All three hospitals publish
  one. Ingestion reads it for the target `location-name`, so a hospital that moves or renames its file is picked up, and
  the move is logged. *Rejected:* hard-coding the three URLs above. UChicago's path already has a year in it
  (`/2026/`), so hard-coded URLs would rot on the next annual refresh.
- **Change detection hashes the file contents, not HTTP headers.** Rush's vendor endpoint sends no `Content-Length` or
  `Last-Modified`. SHA-256 of the body is the one rule that works for all three.
  *Rejected:* `ETag`/`Last-Modified`, which is unavailable for Rush.
- **Consequence for M1 (recorded, not yet built):** NM is 5 GB of pretty-printed JSON, so the JSON parser must stream
  (`ijson`, C backend: a full count pass took 61 s). Loading it whole is not an option. M1's "one hospital end to end"
  should start with **UChicago** (42 MB, JSON, has the BOM quirk) or **Rush** (the CSV path). NM comes in M2 once the
  streaming parser has proven itself.
- **Coverage fact, not a defect:** only 14,665 of UChicago's 49,438 items (30%) carry any payer-specific rate; the rest
  are gross/cash only. This limits the M7 comparison and will be stated there.

Schema deviations found while confirming these files are recorded in `docs/schema-drift-log.md` (the first 6 entries).

## CP-DEC 006 — Three normalisation rules: rate precedence, zero-count medians, code family (2026-09-29)
**Status:** Decided (Trevor set the direction; the specifics follow the CMS v3 data dictionary). Implemented in
`clear_pricer/rules.py`, unit-tested, and enforced by dbt gates.

1. **The dollar amount is the price whenever it's present.** When a row has both a dollar and a percentage,
   `negotiated_rate` = the dollar. The spec backs this: *"If a payer-specific negotiated charge dollar can be
   calculated … calculate the dollar amount and encode [it]."* `rate_basis` records where the dollar came from:
   `dollar` · `dollar_from_percent` (the dollar is within one cent of pct × gross, i.e. just a percentage applied to the
   chargemaster price) · `dollar_percent_unreconciled` · `percent_only` · `algorithm_only` · `missing` · `no_payer`.
   *Why:* on Rush, 68,877 of 74,067 dual rows are exactly pct × gross (truncated or rounded). A comparison can then
   choose to exclude chargemaster-derived dollars instead of mixing them in silently.
   *Rejected:* percentage wins (it discards the spec's preferred encoding and yields no comparable dollar), and
   dropping dual rows (that silently loses 36% of Rush's data).
2. **Allowed amounts reported with `count = "0"` are nulled, not zeroed.** The spec says *"If the count of allowed
   amounts is zero, do not encode these data elements."* A median over zero remittances is not a fact. Zero would claim
   a $0 payment; null says "unknown". The clean columns (`median_amount`, `p10_amount`, `p90_amount`) are nulled and
   `allowed_amounts_suppressed = 'count_zero'` is set. The raw values stay in `*_raw` columns, and each occurrence is
   counted in the drift log. *Rejected:* setting them to zero (it creates false $0 prices); dropping the row (the
   negotiated rate on the same row is still valid).
3. **`code_family` comes from the code's shape, following the standard.** HCPCS Level I *is* CPT (AMA): Category I is
   `\d{5}`, Category II `\d{4}F`, Category III `\d{4}T`, PLA `\d{4}U`. HCPCS Level II (CMS) is a letter plus 4 digits,
   and `D\d{4}` is CDT (dental). The declared type is kept verbatim, and `type_conflict` flags codes whose shape
   contradicts it (`cpt_declared_not_cpt`, e.g. NM's `A4216` typed `CPT`). **A CPT code typed `HCPCS` is not a
   conflict**, because Level I is part of HCPCS. That's what Rush and UChicago do, and it's valid. Comparisons join on
   `code_family`. *Rejected:* trusting the declared type (it would split the same procedure across hospitals), and
   rewriting the declared type (that loses what the source actually said).

## CP-DEC 007 — M1 pipeline shape and gate policy (2026-09-29)
**Status:** Decided.

- **Landing → staging → dbt.** `curl` lands each file content-addressed at `raw/hpt/<hospital>/<sha256[:16]>/`.
  A pure Python parser writes staging Parquet (`charges`, `codes`, `drift`, `quarantine`, `file`), and dbt-duckdb
  builds the views and marts and runs the tests in one `dbt build`.
  *Rejected:* DuckDB's `read_csv` straight into dbt. It can't record per-column unmappable fields or quarantine
  ragged rows with their raw cells (design pin 3), and it would put the spec rules in SQL, where they can't be unit
  tested.
- **Two classes of check.** *Integrity gates* **fail the run**:
  - a required CMS column or header field is missing;
  - the template version isn't `3.0.0`;
  - more than 1% of records are quarantined (`max_quarantine_share`);
  - rows are lost (read ≠ published + quarantined, or the file is empty);
  - a key is not unique, an enum is invalid after normalisation, or a relationship is broken;
  - a zero-count median leaks into the clean columns;
  - `negotiated_rate` is inconsistent with `rate_basis`.

  *Source-conformance findings* (a hospital's own spec deviations: unreconciled dual rates, zero-count medians,
  unmapped extra columns) are **measured and published** in `rpt_source_conformance`. They don't fail the run.
  *Why:* the spec allows hospital-created columns, and a gate that fails on every publisher defect would never go
  green. The deviations *are* the dataset's story (CP-DEC 003), so they're published as numbers.
  *Rejected:* dbt `warn` severity for either class. Nothing in the project warns (design pin 4).
- **Idempotency is byte-level.** Staging carries no timestamps and keeps a stable row order, so the same landed file
  yields byte-identical Parquet. This was verified by hashing the outputs of two runs. The only non-deterministic field
  is `first_seen_at` in `landing.json`, and it's written once.
- **The first hospital end to end is Rush** (CSV tall). It has both publisher anomalies, so every CP-DEC 006 rule is
  exercised on real data; UChicago has neither.

## CP-DEC 008 — M2: streaming JSON, the item grain, Airflow 3, and gated publish to Postgres (2026-09-29)
**Status:** Decided.

- **Both parsers stream.** Rows go to Parquet in 100k-row batches, and JSON is parsed in one `ijson` pass that holds
  one `standard_charge_information` item at a time. NM (5 GB, 7.03M payer rows) stages in about 2m45s in bounded
  memory. *Rejected:* `json.load` (the whole 5 GB in RAM) and DuckDB `read_json` (it can't log per-field drift or
  quarantine malformed items with their raw content).
- **Codes attach to items, not charge rows.** A JSON item's codes apply to every payer row under it (about 60 at NM),
  so keying codes by charge row would multiply NM's 372,886 codes to about 20M. `dim_charge_codes.item_id` joins to
  `fct_standard_charges.item_id`. In CSV tall, each row is its own item.
- **Conditional download with the content hash as identity.** Where the server sends an ETag (NM, UChicago), `curl
  --etag-compare` turns an unchanged 5 GB file into a `304` and one request. Rush sends no ETag, so it downloads
  (69 MB) and the hash decides. Manifests store paths relative to `data/`, so host and container runs share one landing
  zone. *Rejected:* `Last-Modified` alone (it can't be compared exactly) and always re-downloading (5 GB/day for no
  change).
- **Staging is written aside and swapped in.** A parse that dies halfway never leaves a partial hospital for dbt to
  read.
- **Airflow 3.3.2, LocalExecutor, one Postgres server.** Postgres 16 hosts two databases: the served `clear_pricer`
  and Airflow's metadata. The pipeline runs in its own virtualenv inside the Airflow image, built from
  `requirements.lock`, so dbt and DuckDB pins never fight Airflow's constraints. The repo is bind-mounted, so code
  changes don't need a rebuild. Host ports are 5433/8081 because 5432/8080 are used by other local stacks. Local dev
  auth: every user is admin, and credentials are local-only defaults overridable via `.env`. *Rejected:* Airflow 2.x
  (3.x is current), and installing dbt into Airflow's own environment (dependency conflicts).
- **DAG shape:** `stage_{rush,uchicago,nm}` (parallel) → `dbt_build_gates` → `publish_postgres`. The gates are the
  dbt tests, and a failure makes the run red. Publish is downstream of the gates, so **Postgres only ever holds a gated
  build**. Stage tasks retry twice (network only), and the gate and publish tasks never retry, so a failure can't be
  retried until it disappears.
- **Publish = copy, swap, prove parity.** DuckDB's Postgres extension copies the marts into `published_new`, and a
  single transaction swaps it for `published`, so readers never see a half-copy. Parity then runs the same aggregates
  natively in both engines: counts and distinct keys must match exactly, and money sums must match to 1e-9 relative.
  Summing 7M floats in a different order changes the 11th significant digit. Any mismatch fails the task.
  *Rejected:* dbt-postgres running the same models (the staging models read Parquet, which Postgres can't) and
  `pg_dump` from DuckDB (not available).

## CP-DEC 009 — Hosted schedule: GitHub Actions cron, not an always-on Docker host (2026-09-29)
**Status:** Decided (built in Milestone 6). Raised by Trevor: the pipeline must not depend on Docker running on his
machine.

- **The hosted daily run is a scheduled GitHub Actions workflow** running the same CLI steps as the Airflow DAG:
  `stage` × 3 → `build` (dbt gates) → `publish`. A failing gate fails the workflow and nothing is published. Outputs go
  to the M6 targets: Parquet to a GitHub Release and the served tables to Supabase (v2). State that must survive
  between runs (landing manifests + ETags, and the NPPES history from M3) persists outside the runner.
- **Airflow stays as the local orchestration** (`docker compose up`), demoable on demand. Both schedulers are thin
  wrappers over one CLI, so there's a single pipeline and no drift between them.
- **Cost:** $0. A daily run is about 10 minutes, far inside the free Actions minutes even for a private repo.
- *Rejected:* managed Airflow (MWAA / Cloud Composer: several hundred $/month for one daily DAG); an always-on VM
  running compose (about $5/month, but a server to patch and watch for one job); keeping Docker up locally (Trevor
  doesn't want a machine dependency).

## CP-DEC 010 — NPPES: stream-and-project, SCD2 CDC keyed on the record's own dates, state kept apart (2026-09-29)
**Status:** Decided (Milestone 3).

- **Stream and project, don't extract.** The monthly V2 file is 1.16 GB zipped / 11.7 GB of CSV, 330 columns. pyarrow
  streams `npidata_pfile_*.csv` straight out of the zip, keeps the 51 columns this project uses (21 attributes + 15
  taxonomy slots × code/switch), and DuckDB types and hashes them. 9.8M providers take about 1 minute; nothing
  11.7 GB touches disk. The other ~280 columns are deliberately not modelled, but **the header is checked in full**:
  a missing projected column rejects the file (gated), and a changed column count is logged.
  *Rejected:* unzip then `read_csv` (11.7 GB of scratch disk every month for columns we drop), and modelling all 330
  columns (scope with no consumer).
- **CDC = SCD type 2 in `provider_history`.** A new version is written only when a provider's attributes (row hash)
  or status change. The previous version is closed with `valid_to`, and the history is never truncated (design pin 2).
  The monthly full file is *diffed* against current state, not reloaded; NPIs missing from a full file are
  tombstoned `absent_from_full`, not deleted.
- **Ordering comes from the record's own dates, not from arrival.** Effective date = the latest of Last Update /
  Deactivation / Reactivation date. An incoming record older than the current version is `stale` and skipped, so a
  late or re-run older file can't roll state back. Two file-level guards come on top: a weekly file entirely covered
  by the last applied full file is `superseded_by_full`, and file coverage comes from the CSV's own name
  (`npidata_pfile_20050523-20260913.csv`), not the zip name.
- **Deactivation stubs carry the last known identity forward.** NPPES publishes a deactivation as an NPI and a date
  with every other field blank. The new version keeps the prior attributes, takes the deactivation from the stub, and
  is `status = deactivated`. *Why:* a price file can cite an NPI that has since been deactivated; M4 needs to know who
  it *was*. *Rejected:* taking the stub as published (it would blank the provider's history).
- **Hash definition:** the attributes that define a provider, excluding Last Update and Certification dates, so a
  re-certification with no change isn't a new version. The hash is recomputed from the *resolved* attributes, so a
  re-applied file reproduces it exactly (an earlier draft hashed a hash for stubs and would have logged spurious
  updates on every re-run).
- **State lives apart from the warehouse.** `nppes_state.duckdb` holds the history; the dbt warehouse is disposable
  and rebuilt each run. dbt attaches the state DB read-only and gates its invariants: one current version per NPI;
  versions 1..n with no gaps; valid intervals; no rejected-schema files; NPIs are 10 digits.
- **The served store gets the file log, not the registry.** `rpt_nppes_file_log` is published with parity. The
  9.8M-row registry is not re-served, because CMS already publishes it; the served store holds what this project
  adds (the change ledger now, reconciliation results in M4).

## CP-DEC 011 — NPI reconciliation is two-directional; candidates, not accusations (2026-09-29)
**Status:** Decided (Trevor approved the approach; Milestone 4).

- **Context.** CMS v3 price files carry NPIs in exactly one place: the header's `type_2_npi` (the hospital's own
  Type 2 NPIs). v1's three hospitals disclose **8**. A one-directional "unresolved rate" over 8 NPIs is a thin number.
- **Direction 1 — resolution (price file → NPPES).** Each disclosed NPI is checked for format and **check digit**
  (Luhn over `80840` + NPI), presence in NPPES, active status, entity type 2, **hospital taxonomy** (primary taxonomy
  27x/28x, the CMS criterion), name similarity to the file's hospital/location names, and address/ZIP agreement. It
  also gets its status **as of the file's `last_updated_on`** via the SCD2 history (NULL when the history starts
  later). Outcome ladder: `invalid_npi` → `unresolved` → `resolved_inactive` → `resolved_not_organization` →
  `resolved_not_hospital_taxonomy` → `resolved_identity_mismatch` → `resolved_verified_campus` →
  `resolved_verified`. **Unresolved rate** = (`unresolved` + `invalid_npi`) / disclosed.
- **Direction 2 — completeness (NPPES → price file).** CMS requires the file to list the Type 2 NPIs of the hospital
  *and all its locations*. Candidates are active, Type 2, hospital-taxonomy NPIs in a disclosed ZIP with **name
  similarity ≥ 0.90** (Jaro-Winkler on normalised names, org or parent org). Tier 1 = same street address (house number
  + street + ZIP); tier 2 = same campus ZIP. Tier 3 (same address, different name) and tier 4 (near-miss, 0.80–0.90)
  are published, **not counted**. **Disclosure coverage** = disclosed / (disclosed + undisclosed tier 1–2 candidates).
- **Candidates, not violations.** NPIs are rarely deactivated, so an undisclosed candidate may be a stale or billing
  registration. Every candidate is published with its evidence (name, taxonomy, address, similarity), and the report
  carries a **threshold sensitivity** table (0.85 / 0.90 / 0.95). *Why:* a number with its evidence and its
  sensitivity can be checked; an accusation can't.
- **The threshold (0.90) was fixed before any result was seen,** and it's reported with its sensitivity rather than
  tuned. It cuts both ways: "UNIVERSITY OF CHICAGO HOSPITALS" (0.886–0.895) falls below it, and "RUSH UNIVERSITY"
  (0.900) sits on it.
- **Matching is deterministic SQL macros** (`dbt/macros/reconcile.sql`), unit-checked on known inputs inside the gate
  run (including the CMS standard's check-digit example `1234567893`).
- **Reproducible figure.** `clear-pricer report` regenerates `docs/results/npi-reconciliation.md` from the gated marts,
  pinned to the input SHA-256s and NPPES files; the same inputs give byte-identical output.
- *Rejected:* resolution-only (a thin number); pulling more NPIs from sister hospitals' price files (edges past the
  CP-DEC 002 hospital line); probabilistic record linkage (opaque for a published figure at this scale);
  treating NPPES secondary practice locations as addresses (not modelled in v1; noted as a limitation).
- **Gates:** every disclosed NPI gets exactly one resolution row; every counted candidate satisfies the stated rule;
  the headline row adds up; the macros pass their unit checks. The rates themselves are published, never gated:
  they're findings, not failures.

## CP-DEC 012 — The FHIR path: pinned Synthea, measured mapping, synthetic-only as a gate (2026-09-29)
**Status:** Decided (Milestone 5).

- **Generation is pinned and containerised.** Synthea **v4.0.0**, pinned by jar SHA-256, runs in `eclipse-temurin:21-jre`,
  so no local Java is needed. Seed `20260929`, clinician seed `20260929`, reference date `2026-09-01`, 200 living
  patients, Chicago IL. Output content is **byte-deterministic** (verified twice). Only the directory files'
  *names* embed a wall-clock timestamp, so staging orders bundles by content hash, never by name. The
  population is generated once on demand (`clear-pricer synthea-generate`); the DAG only *stages* it.
  *Rejected:* the moving `master-branch-latest` build (not reproducible), and committing generated bundles (681 MB;
  `data/` is gitignored and they regenerate exactly).
- **"Mapped" is measured, not declared.** Each resource is wrapped in a read-tracking view, and a leaf counts as mapped
  only if an extractor actually read it. The mapping report (`rpt_fhir_mapping`, `docs/results/fhir-mapping.md`)
  therefore can't drift from the code. 11 of 24 resource types are modelled, and the other 13 are reported at 0%, not
  hidden. *Rejected:* a hand-maintained mapping spec (it would go stale the first time an extractor changed).
- **Validation = structural + referential, not full profile conformance.** Checks cover the R4 1..1 elements this
  project uses, coding `system`+`code`, date formats, and **every reference resolving**: `urn:uuid` within its bundle,
  conditional `Type?identifier=system|value` against the synthetic provider directory, and `#id` against `contained`.
  *Rejected:* the HL7 FHIR Validator (a Java tool with full US Core profile validation). It's heavier than this
  milestone's need and would validate Synthea against profiles it is built to, which proves little.
- **No PHI, by construction, as a gate** (design pin 1). `assert_fhir_synthetic_only` fails the run unless *every*
  Patient carries Synthea's identifier system, an SSN in the SSA-never-issued 999 range, and digit-suffixed names.
  Tests prove one real-looking patient turns the run red. Staging keeps only those **flags**: no names, SSNs or
  street lines, even though they're synthetic.
- **Integrity gates vs. published findings,** as in CP-DEC 007. Unresolved references, non-bundle files, id-less
  resources, and modelled resources lost between parse and table all fail the run. Missing optional content and
  unmapped paths are published.
- **The code bridge is measured, not assumed.** Synthea bills in SNOMED / LOINC / RxNorm / CVX / CDT / ICD-10; price
  files in CPT / HCPCS / CDT / MS-DRG / NDC / RC. `rpt_fhir_code_bridge` states the only overlap (CDT) as numbers.

## CP-DEC 013 — Serving: Parquet release for detail, Supabase free tier for the small set, FastAPI over Parquet (2026-09-29)
**Status:** Decided (Trevor chose the free tier; Milestone 6).

- **Three read paths, each for what it's good at:**
  1. **The GitHub Release (Parquet)** is the full detail, including the 7.37M-row fact. It's queryable with DuckDB
     straight over HTTPS, with no clone, account or credentials (design pin 5). Exports are byte-deterministic.
     `manifest.json` pins the inputs (price-file hashes, NPPES files, Synthea version) and every output hash; its
     fingerprint covers both, so a release is cut **exactly when the published data would change** (new upstream
     data, or a logic fix that moves a number) and never otherwise.
  2. **Supabase (free tier)** hosts the *served set*: the reconciliation/conformance/FHIR reports plus
     `agg_code_prices` (49,404 rows), a few MB against a 500 MB limit. Its auto-generated REST API is the zero-ops
     hosted endpoint. *Rejected:* Pro at $25/month to serve the full fact (the Parquet release already serves it
     better). The upgrade triggers are recorded: serving the full fact over the API, a no-pause guarantee, or backups.
     Switching is a connection string only.
  3. **FastAPI over the release Parquet** (in-memory DuckDB, parameterised read-only SELECTs). It's self-hostable
     anywhere the files are, with charge-level lookups by code. *Not yet deployed to a host:* that needs a hosting
     decision (and possibly cost), deferred.
- **Served tables are locked down on every publish.** Each served table gets row-level security, one read-only
  policy and `SELECT` for `anon`/`authenticated`, applied **inside the same transaction as the schema swap** (a swap
  drops grants and policies). Project settings: automatic RLS on, auto-expose new tables off; only the `published`
  schema is exposed to the Data API.
- **Credential hygiene is enforced in code.** DSNs come only from the environment or `.env` (gitignored), never the
  command line. Every connection runs under `secrets.guard`, which strips passwords from driver errors, because
  libpq echoes the full DSN when a connection fails. *Why:* this happened during setup (see BUILD-LOG); the fix is
  regression-tested. Hosted runs read the DSN from a GitHub Actions secret the owner set directly.
- **`agg_code_prices` keeps `rate_basis` as a grouping key**, so a consumer compares contracted dollars
  (`rate_basis = 'dollar'`) and isn't silently mixing in chargemaster-percentage dollars (CP-DEC 006).
- **Release-type gate:** Parquet has no 128-bit integer, so DuckDB writes HUGEINT as DOUBLE (counts became `8.0`).
  No release table may carry a HUGEINT column.
- **Hosted pipeline state:** the NPPES CDC history persists between GitHub runs in the Actions cache. If it's evicted,
  the run rebuilds from the files CMS currently lists, and that shows in `rpt_nppes_file_log`. *Rejected:* a 1.5 GB
  state file as a release asset (heavy daily upload), and rebuilding every run (history resets monthly).

## CP-DEC 014 — Reproducible across machines, not just across runs (2026-09-29)
**Status:** Decided. **Corrects CP-DEC 012,** which called Synthea output "byte-deterministic (verified twice)". That
check covered 3 patients, then 2 sample files, and missed a thread-scheduling race.

- **What exposed it.** The first hosted release (GitHub runner, Linux, 4 CPUs) and the local build (Windows Docker,
  16 CPUs) had **identical inputs**, but 3 of 13 release files differed. 10 were byte-identical, including the
  7.37M-row fact. Each cause was diagnosed row by row before any fix:
  1. `agg_code_prices`: `any_value(description)` picks whichever row a thread reaches first. Only
     `example_description` differed (2,308 rows); every price statistic matched. → `min(description)`, and every
     other `any_value` was replaced with an explicit aggregate.
  2. `rpt_fhir_mapping`: one synthetic patient's CarePlan had one extra activity. **Synthea is not
     byte-deterministic when multi-threaded**: two identical 16-core runs on the same machine differed in one
     patient file, and 16-core vs single-CPU runs differed in 11. Single-CPU runs are identical. → Synthea's container
     is pinned to `--cpus=1` (about 80 s instead of 22 s for 200 patients).
  3. `rpt_nppes_file_log`: the local ledger included the idempotency-proof re-applications (all zero-change); the
     hosted one didn't. → Publish one row per file (its first application); re-applications stay in staging for
     audit.
- **The standard is now "the same inputs give the same bytes on any machine."** The check is the release
  fingerprint (inputs + output hashes): a hosted build and a local build of the same upstream data must produce the
  same fingerprint.
- *Rejected:* excluding the FHIR tables from the fingerprint (hides the problem rather than fixing it); running
  Synthea multi-threaded and staging a canonicalised sort (the difference is in content, not order).

## CP-DEC 015 — What counts as "the same price" for the M7 comparison (2026-09-29)
**Status:** Decided (Milestone 7). Refines CP-DEC 006 for *comparisons*: the dollar is still the price, but only some
dollars are the same unit across hospitals.

- **Found before writing:** `rate_basis = 'dollar'` mixes unlike things. Rush's 81,388 `other`-methodology dollars
  sit at a median 1.00 × list price, and 39,116 of them are seven Medicare Advantage plans listed *at* the
  chargemaster. Northwestern's dollars are mostly case rates and per diems (packages), and its 323 fee-schedule
  dollars include $0.01 for a $1,361 venipuncture. A first cut that trusted `rate_basis` alone made Rush look like
  the most expensive hospital on 398 of 534 codes. That's an artifact.
- **The comparable set:** CPT Category I by *derived* family; outpatient including `both`; line items only
  (Northwestern's 2,124 `CASE-` package items are excluded); "unlisted" codes excluded; per hospital × code, the median
  across rows.
- **Three price concepts, never mixed:** list (gross) and cash for all three hospitals. Contracted = fee-schedule
  dollars only (`rate_basis = 'dollar' AND methodology = 'fee schedule'`), **Rush vs UChicago only**, because
  Northwestern's fee-schedule dollars can't be validated from inside its file. A negotiated rate equal to the list
  price is never counted as contracted.
- **Contracted per hospital × code** = the median across payers of each payer's median. It doesn't let one payer with
  many rows dominate.
- **Every number is computed, none typed.** `clear-pricer analysis --release <tag>` regenerates the document, figures
  and JSON from a public release, and the committed page is generated from `data-2026-09-29-27a34004`. A local run and
  a release run produced identical results. Tests enforce the exclusions and byte-reproducibility.
- *Rejected:* weighting by volume (no claims data; stated as a caveat); a composite "hospital price index" (it would
  hide the payer-spread finding that is the story); including Northwestern's contracted dollars with a warning
  (numbers known to be broken would still get quoted).

## CP-DEC 016 — v1 shipped with v2's features at three-hospital scale; v2 is the scale-out (2026-09-29)
**Status:** Decided (Milestone 8). Records a scope fact; doesn't re-open CP-DEC 002.

- CP-DEC 002 put NPPES reconciliation with an unresolved rate, a public read API and the price-variation analysis in
  v2. The milestone spine (TODO M3–M7) built all of them at v1's three-hospital scale, with every gate in place first,
  so "cut hospitals, not verification" held.
- **What v1 can't do is what scale buys:** the unresolved-NPI rate is over 8 NPIs, and the same-code comparison
  speaks for three hospitals, not the metro. **v2 is therefore defined as scale-out**: 50+ hospitals (every one
  through the same gates, the same drift log and the same release fingerprint), plus a publicly hosted API.
- *Rejected:* relabelling v1 as "v2 done" (the metro-level claims need the hospitals), and deferring the features to
  keep the original labels (they were built and verified; hiding that would misstate the system).

---
*Next entry = CP-DEC 017.*
