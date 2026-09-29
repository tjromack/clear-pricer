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

---
*Next entry = CP-DEC 011.*
