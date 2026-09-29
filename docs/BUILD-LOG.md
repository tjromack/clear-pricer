# clear-pricer — build log

A running, dated record of noteworthy process, decisions, learnings, and what-broke moments as clear-pricer is built.
**The primary raw material for the case study and any other writing** — kept honest and specific, numbers only when
measured. Per `CLAUDE.md`, keeping this current is part of each milestone's definition of done.

Entry template: **What happened · Decisions · Learnings · What broke (+ fix) · Open/next.**

## Index (kept current)

| Milestone | Commit | Headline (measured) | The break worth telling |
|---|---|---|---|
| 0 · confirm files | `7c48393` | 3 hospitals, all CMS v3.0.0; NM = 5.02 GB JSON | UChicago JSON starts with a BOM; Rush's `cms-hpt.txt` URLs have no scheme |
| 1 · Rush end to end | `86fe81d` | 208,409 rows, 0 quarantined; 3 broken fixtures each trip their gate | float-cent reconciliation mis-flagged 1,037 truncated dollars |
| 2 · 3 hospitals + Airflow | `e2fcf35` | 7,371,416 rows; red DAG on bad input, Postgres untouched | 55.9% of NM's dual-rate dollars don't reconcile (1.40M = a zero-count median) |
| 3 · NPPES CDC | `b4c379d` | 9.8M providers from 11.7 GB in 57 s; re-apply leaves 9,855,257-row history identical | the full file "through 09/13" carries 09/14 records; the idempotency proof caught a flip-flop |
| 4 · NPI reconciliation | `736969e` | unresolved 0.0%; disclosure coverage 13.3% (52 candidates, 46–65 sensitivity) | views baked in absolute paths; the warehouse broke across host/container |
| 5 · synthetic FHIR | `f5d2c42` | 240,237 resources; 1,200,521/1,200,521 refs resolve; no-PHI gate | 43,606 "dangling" refs were `#contained` references |
| 6 · publish + serve | `ad54b89` | release fingerprint identical on GitHub runner and workstation; public | a DSN password leaked into a session (rotated; redaction added); Synthea not deterministic multi-threaded |
| 7 · the analysis | (M7 commit) | list 2.11× median across 2,323 codes; within-UChicago payer spread 4.06× vs 1.55× Rush–UChicago | a first cut made Rush "most expensive" — 39,116 MA rates published at list price |

Where things are: decisions → `DECISIONS.md` (CP-DEC 001–015) · upstream deviations → `docs/schema-drift-log.md` ·
reproducible figures → `docs/results/` (`clear-pricer report`) · the analysis → `docs/analysis/` · how to query → `docs/QUERY.md`.

---

## 2026-09-28 — Phase 0 (scaffold + kickoff)

### What happened
- Initiated **BUILD 2** of the portfolio slate: `clear-pricer`, a public healthcare price & provider data platform —
  the **data-engineering pillar**, deliberately its own repo *outside* the AI workspace (`C:/dev/clear-pricer`,
  `tjromack/clear-pricer`, private until ready).
- Decisions-first, then scaffolded the skeleton (the kickoff-packet approach): `DECISIONS.md`, `CLAUDE.md`, `README.md`,
  `TODO.md`, `.gitignore` (data ignored), `LICENSE` (MIT), `data/README.md`. Committed + pushed.

### Decisions (CP-DEC 001–004)
- **Chicago** metro; three candidate hospitals — **Northwestern Memorial · Rush · UChicago Medicine** — treated as
  candidates to *confirm*, not blind picks (Milestone 0).
- **v1/v2 split** as the anti-sprawl guardrail (the playbook's #1 named failure mode for this build): v1 = 3 hospitals
  shipped with working gates; v2 = 50+ hospitals + NPPES reconciliation + public API + a CPT price-variation analysis.
  **Cut hospitals, not verification.**
- **Postgres-in-Docker** local + **Supabase** served (v2); **MIT** license (a tool over public data).
- **Verification-first:** gates *fail the DAG* (not warn); the schema-drift log and the unresolved-NPI rate are the
  credibility, written as they happen.

### Learnings / banked context
- **The TLS-proxy gotcha (CP-DEC 004):** this machine runs a TLS-inspecting proxy — Python `requests`/`httpx` fail cert
  verification; `curl` (OS trust) works. All CMS/NPPES downloads go through `curl` or with `truststore.inject_into_ssl()`
  injected first. (Learned the hard way across the sibling `spancheck` builds.)
- **Data hygiene from day one:** `data/` subtrees are gitignored in full; only `data/README.md` is tracked; the
  published Parquet ships via a GitHub Release, never the repo.

### Open / next
- **Milestone 0 — confirm the three hospital files:** locate each hospital's CMS machine-readable file (standard-charges
  page, EIN-based filename), fetch via `curl`, confirm it downloads + parses; swap any that's missing/broken; lock the
  final three in `DECISIONS.md`. Then Milestone 1 (one hospital end-to-end). Each milestone ends at a quality gate +
  commit + stop.

---

## 2026-09-28 — Milestone 0 (confirm the three hospital files)

### What happened
- **Found every file through `cms-hpt.txt`, not by searching.** CMS requires each hospital to publish `/cms-hpt.txt`
  at its site root, listing `location-name`, `source-page-url` and `mrf-url`. NM, Rush and UChicago all have one, so
  none of the three files had to be hunted down on a web page. Each `cms-hpt.txt` covers the whole system (NM lists 7
  hospitals, Rush 3, UChicago 3), which makes it the natural discovery point for the v2 scale-out.
- **Checked sizes with `HEAD` before downloading.** UChicago is 42 MB and Rush 69 MB, but **NM is 5.02 GB** of
  pretty-printed JSON. I had agreed to check in above ~5 GB, so I first pulled 2 MB from each end with HTTP range
  requests (`206`). Both ends were well-formed CMS v3.0.0, with the header keys at the top and `modifier_information`
  closing the file. Trevor chose the full download plus a streaming parse over "head/tail is enough" or swapping NM out:
  v1 has to ingest it anyway, and a gate built on an inference isn't a gate.
- **Downloaded all three in full with `curl`**, hashed them (SHA-256 in CP-DEC 005), and parsed each end to end:
  UChicago with `json` (after the BOM fix below), Rush with `csv` (208,409 rows, 0 ragged), NM with an `ijson`
  stream (61 s: 118,403 items, 7,026,150 payer rows). None needed swapping.
- **Profiled what matters downstream:** code-type mix, methodology mix, `count` encoding, Type-2 NPIs in each header
  (NM 2, Rush 1, UChicago 5; these are the first M4 reconciliation inputs), and payer coverage.

### Decisions (→ CP-DEC 005)
- **v1 locked: NM · Rush · UChicago**, all CMS template v3.0.0.
- **Discovery reads `cms-hpt.txt`; hard-coded URLs rejected.** UChicago's MRF path already has `/2026/` in it, so a
  hard-coded URL breaks on the next annual refresh.
- **Change detection hashes the body; HTTP headers rejected.** Rush's vendor endpoint returns no `Content-Length` or
  `Last-Modified`, so body hashing is the only rule that works for all three sources.
- **M1 starts with UChicago or Rush, not NM.** The 5 GB file comes in once the streaming parser is proven.

### Learnings
- **All three hospitals follow the same template (v3.0.0) but encode it differently.** The six drift-log entries
  written today came from reading headers and counting fields, before any pipeline code existed.
- **CPT-vs-HCPCS typing can't be trusted, even within one file.** Rush and UChicago never use `CPT`, and NM uses it
  in both directions (108 numeric codes typed `HCPCS`, 1,037 `A1234`-shaped codes typed `CPT`). The M7 "same CPT
  across the metro" comparison has to join on a code family derived from the code pattern, not on the declared type.
- **Dollar values in these files are mostly not negotiated dollars.** 6.80M of NM's 7.03M payer rows are
  `percent of total billed charges`, and 6.62M carry a dollar *and* a percentage. Without an explicit precedence rule, a
  naive "negotiated price" column would mix derived and contracted amounts.
- **The medians can contradict their own counts.** 93% of NM payer rows and 72,831 Rush rows give a median/10th/90th
  with `count = "0"`. UChicago is clean on both counts, so the defect belongs to specific publishers, not the template.
- **Coverage is thinner than the row count suggests:** only 30% of UChicago items (14,665 / 49,438) carry any
  payer-specific rate.
- **Proxy:** `curl` handled every download, including the 5 GB file. `pip install` also went through the proxy without
  trouble (pip ships `truststore`); the CP-DEC 004 failure is specific to `requests`/`httpx`.

### What broke (+ fix)
- **UChicago JSON failed to load:** `JSONDecodeError: Unexpected UTF-8 BOM`. RFC 8259 forbids a BOM in JSON.
  Fix: decode as `utf-8-sig`. The parser will strip a BOM on every source and log that it did.
- **Rush's `cms-hpt.txt` URLs have no scheme** (`apps.para-hcfs.com/…`), which a strict URL client rejects.
  Fix: normalise to `https://` and log the correction.
- **`ijson` wasn't installed.** Installed it into a throwaway venv in the session scratchpad; no project dependencies
  were added in M0.

### Open / next
- **Milestone 1 (awaiting approval):** landing → staging for ONE hospital (UChicago or Rush), with the parser's
  unmappable-field log, dbt tests that fail the run, and DuckDB-local mode.
- Decisions due before staging: dollar-vs-percentage precedence; handling of `count = 0` medians
  (null / quarantine / annotate); the derived `code_family` rules.

---

## 2026-09-29 — Milestone 1 (one hospital, end to end: Rush)

### What happened
- **Settled three rules before writing staging code, checking each against the CMS spec rather than intuition.**
  Trevor's instincts were: the dollar is the price; zero-count medians should be zero or null, whichever is best
  practice; classify code types by a standard. I fetched the CMS v3 CSV data dictionary and the v3 tall template from
  `CMSgov/hospital-price-transparency` (via `curl`) and checked each one:
  - *Dollar vs percentage:* the spec says to calculate and encode the dollar when it can be calculated, so dollar wins.
    Tested against Rush first: 93% of dual rows are exactly pct × gross. So the dollar is the price, but it gets a
    label (`rate_basis`) saying where it came from.
  - *Zero-count medians:* the spec says *"If the count of allowed amounts is zero, do not encode these data elements."*
    Null, not zero. Zero would publish a $0 price that never happened.
  - *Code types:* HCPCS Level I **is** CPT. That corrected my M0 drift entry: Rush and UChicago typing CPT codes as
    `HCPCS` is valid, just ambiguous. Only NM's `A1234`-as-`CPT` is a real conflict.
- **Built the pipeline:** `curl` + content-addressed landing, a pure CSV-tall parser that records rather than drops,
  deterministic Parquet staging, then dbt-duckdb views, marts and gates in one `dbt build`, all behind one command:
  `clear-pricer run rush`.
- **Rush end to end:** 208,409 records read = 208,409 published + 0 quarantined; 604,719 billing codes; 31/31 dbt
  nodes pass. Live run (discovery → curl → stage → dbt) took ~35 s. The file's SHA-256 matched M0's, so Rush hasn't
  republished since yesterday.
- **Proved the gates fail:** three committed broken fixtures (missing `count` column, 10% ragged rows, template
  `2.2.0`). Each trips exactly its intended gate, and `tests/test_e2e_gates.py` asserts a red run for each.
- **Proved idempotency:** two runs over the same file produced byte-identical staged Parquet (SHA-256 of all five files).

### Decisions (→ CP-DEC 006, 007)
- CP-DEC 006: dollar precedence plus `rate_basis`; zero-count allowed amounts nulled with raw values kept;
  `code_family` from code shape.
- CP-DEC 007: a Python parser writes Parquet for dbt (rejected: DuckDB `read_csv` straight into dbt, which can't log
  per-column drift or quarantine rows with their raw cells). Two classes of check: integrity gates fail the run;
  source-conformance findings are measured and published, not failed on. (A gate that fails on every publisher defect
  never goes green, and the defects are the story.) Quarantine tolerance is 1%.

### Learnings
- **The spec settled most "judgment calls".** Every rule landed on a quoted sentence from the CMS dictionary.
  That's more defensible in the case study than "it seemed reasonable".
- **Rush's rows have no gaps, but not much signal:** every one of its 208,409 rows has a payer and a dollar, yet
  68,877 of them are just a percentage applied to the chargemaster price, and 34.9% carry medians over zero
  claims.
- **dbt's version check fails through the proxy** (`dbt --version`: *"latest version could not be determined"*).
  That's the CP-DEC 004 gotcha: dbt uses `requests`. Harmless, but anonymous usage stats (same HTTP path) are
  switched off in `dbt_project.yml`.
- **Windows console encoding:** printing DuckDB results with non-ASCII payer names failed under cp1252.
  Workaround: `PYTHONIOENCODING=utf-8` for ad-hoc queries.

### What broke (+ fix)
- **Float-cent reconciliation.** The first full run reported 6,227 unreconciled dual rows, against the M0 profile's
  5,190. Traced to Rush **truncating** derived dollars (61% × $2.14 = $1.3054 → `1.30`) while my rule **rounded**
  (→ `1.31`), and in floating point `1.31 − 1.30 > 0.01`. The first fix (compare rounded integer cents, ±1) was wrong
  too: a new test showed it accepted a dollar 1.46 cents off. Final rule: within less than one cent of the exact
  product, in cents. That reconciles both truncation and rounding and rejects real mismatches. Counts now match M0
  exactly (68,877 / 5,190), and all three cases are pinned as regression tests.
- **`classify_code` with a missing type** returned no family; the parser test expected shape-based classification.
  Changed so a code with no declared type is classified by shape alone (never forced into `UNCLASSIFIED`), and the
  missing type is logged separately.

### Open / next
- **Milestone 2 (awaiting approval):** a streaming JSON parser (UChicago, then NM's 5 GB), the Airflow DAG with the
  same gates, and Postgres (Docker) parity. Docker is a manual step for Trevor.
- **Clean-clone check passed (after commit `86fe81d`):** `git clone` → fresh venv → `pip install -e ".[dev]"` →
  `clear-pricer run rush` fetched live, staged 208,409 rows and passed 31/31 gates; `pytest` 57/57. No credentials used.
  Automating this as a GitHub Action is M8.

---

## 2026-09-29 — Milestone 2 (all three hospitals, Airflow DAG with failing gates, Postgres parity)

### What happened
- **Environment check first.** Docker 28.3 with 16 CPUs and 33 GB. Host ports 5432 and 8080 were already taken by
  other local stacks (`clinical_trials_db`, nba-parquet's Airflow), so clear-pricer uses **5433/8081** and leaves
  them alone. I then tested the proxy from *inside* a container: image pulls, `pip`, and Python HTTPS (status 200)
  all work. **The TLS-proxy problem is host-only**, and the containers need no certificate workaround.
- **Streaming everywhere.** M1's parser held all rows in lists, which can't work for NM's 7M rows. Refactored both
  parsers into generators around a shared row builder (`normalise.py`), so the CP-DEC 006 rules apply identically to
  CSV and JSON. Staging now streams 100k-row batches to Parquet. Wrote the JSON parser against the **official v3 JSON
  schema** (fetched from `CMSgov/hospital-price-transparency`), not against what the files happened to contain.
- **Changed the grain for codes.** JSON codes belong to an item that has about 60 payer rows at NM. Keyed by charge
  row, NM's 372,886 codes would have become about 20M rows. Codes now key to `item_id`.
- **All three hospitals end to end:** Rush 208,409 + UChicago 136,857 + NM 7,026,150 = **7,371,416 charge rows**,
  1,092,359 codes, 37 modifier rules, **0 quarantined**, 34/34 dbt nodes green. NM: 5 GB of JSON → 109 MB of Parquet;
  the first run took 6m20s including the download.
- **ETag conditional fetch.** Every NM stage would have re-downloaded 5 GB. NM and UChicago send ETags (Rush doesn't),
  so `curl --etag-compare` turns an unchanged file into a `304`. The NM re-stage then took 2m45s of parsing and no
  download. The content hash stays the identity.
- **Airflow 3.3.2 + Postgres 16 in Docker**, with the pipeline in its own virtualenv inside the image. DAG:
  `stage_{rush,uchicago,nm}` → `dbt_build_gates` → `publish_postgres`.
  - **Green run** (`scheduled__2026-09-29`): 4m34s. Inside the container, UChicago and NM got `304` and reused the
    files the *host* had landed, so the two environments share one landing zone. Parity passed in the container too.
  - **Red run** (`broken_input_proof_1`, conf `{"source_overrides": {"rush": "tests/fixtures/broken_ragged_rows.csv"}}`):
    `stage_rush` quarantined 2 of 20 rows, **`dbt_build_gates` failed** on `assert_quarantine_within_tolerance`, and
    `publish_postgres` was **`upstream_failed`**. Postgres was unchanged before and after: 7,371,416 rows and the same
    three source SHA-256s. A bad input can't reach the served store.
  - A clean restore run (`restore_after_proof_1`) followed.
- **Parity:** DuckDB and Postgres agree exactly on counts and distinct keys for all five published tables. Money sums
  (e.g. Σ `negotiated_rate` = 29,076,008,619.41) differ only in the 11th significant digit, because each engine adds
  7M floats in a different order. The check uses a 1e-9 relative tolerance for sums and exact equality for counts.

### Decisions (→ CP-DEC 008)
- Streaming parsers; codes at item grain; ETag conditional download with the hash as identity; staging written
  aside and swapped in; Airflow 3 + LocalExecutor + a separate pipeline venv; publish = copy → atomic schema swap →
  native-SQL parity; Postgres only ever holds a gated build.

### Learnings
- **NM's "negotiated dollars" are mostly not derivable from its own file**, the most important finding so far.
  2.70M dual rows reconcile as pct × gross, but **3.92M (55.9%) don't**:
  - 1.40M equal the *median allowed amount*, a median NM reports over **zero** claims;
  - 2.51M match neither pct × gross nor the median (e.g. gross $833.02 at "100% of billed charges" → $395.91).

  Only **4,140** NM rows are plain contracted dollars. The CP-DEC 006 `rate_basis` label already isolates every case,
  so nothing is mixed silently, but it means NM's clean coverage for the M7 comparison is thin, and that has to be
  stated.
- **The classifier was missing a CPT category.** 13 UChicago codes (`0002M`–`0019M`) are CPT **MAAA** codes (4 digits
  + `M`). Added `CPT_MAAA`. The one genuine anomaly, `7746A`, stays `UNCLASSIFIED` and is published.
- **Real 3-hospital conformance ledger** (`rpt_source_conformance`):

  | Hospital | Zero-count medians | Unreconciled dual rates | Code type conflicts | Other |
  |---|---|---|---|---|
  | NM | 6,523,094 (92.8%) | 3,924,918 (55.9%) | 1,037 | — |
  | Rush | 72,831 (34.9%) | 5,190 (2.5%) | 0 | scheme-less `cms-hpt.txt` URLs |
  | UChicago | 0 | 0 | 1 (`7746A`) | UTF-8 BOM |

  UChicago publishes the most spec-clean file, but only 30% of its items carry a payer-specific rate.

### What broke (+ fix)
- **Image build: `Permission denied: '/opt/cp-venv'`.** The `airflow` user can't create directories under `/opt`.
  Created and `chown`ed the directory as root, then switched back to `airflow`.
- **Compose `depends_on` silently lost Postgres.** YAML merge keys (`<<: *airflow`) are shallow, so each service's own
  `depends_on` *replaced* the anchor's. Caught by reading the config before starting it; the dependency is now
  restated per service.
- **JSON builder bug, caught before the first run.** Inside a top-level object (e.g. `license_information`), ijson's
  `map_key` events share the object's prefix, so my "value finished" check would have closed the object at its first
  key. Only `end_*`/scalar events close a value now, and a test with top-level keys in a different order pins it.
- **Absolute paths in manifests.** `latest.json` stored `C:\dev\...`, which doesn't exist inside the container
  (`/opt/clear-pricer`). Manifests now store paths relative to `data/`. A one-time migration seeded `latest.json` and
  the ETag for files landed before this change, using each file's own manifest and saved response headers, and deleted
  the M0 flat copies after confirming they were SHA-256-identical to the hash-addressed files (about 5.1 GB freed).
- **Publish: `cannot execute DROP SCHEMA in a read-only transaction`.** Opening the DuckDB warehouse read-only made
  *every* attachment read-only, Postgres included. Fix: an in-memory DuckDB session that attaches the warehouse
  `READ_ONLY` and Postgres read-write.
- **Parity: `duplicate column name "count"`.** `postgres_query` returned several aggregates all named `count`. Each is
  now aliased `m0..mN`.
- **A test expectation was wrong, not the code:** an unmapped key on a `standard_charges` object is logged once per
  object, not once per payer row, while the value is still kept on every row.

### Open / next
- **Milestone 3 (awaiting approval):** NPPES full replace + weekly delta with change-data capture. The NPPES monthly
  file is multi-GB (size to be measured in M3), and the same streaming/ETag patterns apply.
- For M7: a sub-flag for "dollar equals a zero-count median", and stating NM's thin clean coverage.

---

## 2026-09-29 — Hosting decision + Milestone 3 (NPPES incremental load + CDC)

### What happened
- **Hosting (raised by Trevor): the pipeline shouldn't need Docker up on his machine.** Decided on a scheduled GitHub
  Actions workflow running the same CLI steps as the DAG (CP-DEC 009, built in M6). Airflow stays as the local, on
  demand orchestration. Cost is $0 against several hundred $/month for managed Airflow. `docker compose stop` is safe
  meanwhile (`catchup=False`, so no backfill storm on restart).
- **Surveyed NPPES before designing.** V2 is now the only format (V1 dropped 03/03/2026). The monthly full is
  1,105.79 MB zipped, holding `npidata_pfile_20050523-20260913.csv` at 11.7 GB and 330 columns. Weekly deltas are
  5.9–6.6 MB. The listing showed an ordering hazard straight away: one weekly (09/07–09/13) sits *inside* the monthly
  file's coverage, so applying it after the full file would roll records back.
- **Checked where the price files carry NPIs.** The CMS v3 schema has exactly one NPI field, the header-level
  `type_2_npi`. So the three v1 hospitals carry **8 NPIs in total** (NM 2, Rush 1, UChicago 5), with none on charge
  rows. Flagged to Trevor as an M4 design question.
- **Built stream-and-project CDC** (CP-DEC 010). pyarrow streams the CSV out of the zip and keeps 51 of 330 columns;
  DuckDB types, derives the primary taxonomy and hashes. **9,798,758 providers in 57 s**, with no 11.7 GB extraction
  to disk. The merge writes SCD2 versions to a state DB that lives apart from the rebuildable warehouse.
- **Real sync:** the September full file was the initial load (9,798,758 inserts); the 09/07–09/13 weekly was
  `superseded_by_full`; then the two later weeklies:

  | Weekly | New | Updated | Deactivated | Reactivated | Unchanged |
  |---|---|---|---|---|---|
  | 09/14–09/20 | 13,665 | 14,073 | 584 | 35 | 5,935 |
  | 09/21–09/27 | 13,547 | 13,868 | 682 | 45 | 5,867 |

  History: 9,855,257 versions across 9,825,970 NPIs. First sync about 5m45s; a no-op sync is one listing request.
- **Idempotency proven on the real data.** I fingerprinted the whole history (count + current count + a hash sum over
  key/version/hash/status/interval columns), re-applied every file with `--reapply`, and fingerprinted again:
  identical. The full file then reports 9,769,907 unchanged + 28,851 stale (records the weeklies had moved past),
  0 changes.
- dbt gates over the 9.8M-row history (one current version per NPI; versions 1..n with no gaps; valid intervals;
  no rejected-schema files; 10-digit NPIs): 46/46 green in 38 s. `rpt_nppes_file_log` publishes to Postgres with parity.

### Decisions (→ CP-DEC 009, 010)
- CP-DEC 009: hosted schedule = GitHub Actions cron over the same CLI; Airflow = local orchestration.
- CP-DEC 010: stream-and-project; SCD2 keyed on the record's own dates with a file-coverage tie-break; deactivation
  stubs carry identity forward; `absent_from_full` tombstones, never deletes; state apart from the warehouse; the
  served store gets the file log, not the registry (CMS already serves that).

### Learnings
- **"Full file through 09/13" is not a hard boundary.** It contains 46 records dated 09/14, and for one NPI it
  disagrees with the 09/14 weekly under the same date. A file's stated coverage is a claim to be checked.
- **Deactivation stubs would erase history** if taken as published: NPI and date, everything else blank. Carrying
  the last known identity forward is what lets M4 say who a deactivated NPI was.
- **About 17% of weekly records are no-ops**: update dates move (re-certification) while the attributes don't. Leaving
  the dates out of the version hash keeps history about real change.
- **Streaming the zip beats extracting it by a wide margin:** under a minute for 11.7 GB of CSV, with no scratch disk.

### What broke (+ fix)
- **Idempotency bug #1, found by reading, before any run.** The first draft hashed a deactivation stub from the
  *current version's hash*, so re-applying would hash a hash and log a spurious update every time. Every row's hash
  now comes from its resolved attributes.
- **Idempotency bug #2, found by reading, before the re-apply proof.** Re-applying an *older* full file after the
  weeklies would have tombstoned the roughly 27k NPIs the weeklies created, since they're "missing" from a full file
  that predates them. `absent_from_full` now only applies to NPIs whose current version predates the file's
  coverage end. Regression test added.
- **Idempotency bug #3, found by the proof itself.** The re-apply added 2 versions. Traced to NPI `1801771704`: same
  Last Update Date (09/14) in the full file and the 09/14 weekly, with different content, so re-application
  flip-flopped it. Fixed with a total order (record date, then file coverage end). The history was rebuilt from
  source, the proof re-run clean, and a regression test pins the case.
- **`enable_progress_bar` can't be set as a DuckDB connect option** ("could not set option as a global option"),
  which killed the first sync after the download. Now set per session. The progress bar had also been polluting logs.

### Open / next
- **Milestone 4 (awaiting approval) needs a design call on what "unresolved NPI" means at v1 scale.** Price files
  carry only header-level Type-2 NPIs: 8 in v1. Options go to Trevor with the M3 report.
- The NPPES monthly deactivation report (2.6 MB) is a candidate cross-check gate (every NPI it lists should be
  `deactivated` in current state).

---

## 2026-09-29 — Milestone 4 (NPI reconciliation, both directions)

### What happened
- **Reframed the milestone before building it.** The v3 schema puts NPIs only in the file header, so v1 has 8 to
  reconcile. I put three options to Trevor: two-directional (recommended), resolution-only, or pulling NPIs from
  sister hospitals' file headers. He approved the recommendation.
- **Direction 1 (resolution): all 8 disclosed NPIs resolve and verify.** All are active, Type 2 and hospital taxonomy
  (282N general acute, 282NC2000X children's, 273R psychiatric unit), with valid check digits and names matching.
  **Unresolved-NPI rate: 0.0%.** Rush's is `resolved_verified_campus`: same name and ZIP, different street.
- **Direction 2 (completeness):** probing NPPES around each hospital's disclosed ZIPs turned up far more active
  hospital-taxonomy registrations under the hospitals' own names than the files disclose:

  | Hospital | Disclosed | Undisclosed candidates |
  |---|---|---|
  | NM | 2 | 17 (9 same address + 8 same campus) |
  | Rush | 1 | 22 (all same campus) |
  | UChicago | 5 | 13 (same address) |

  **Disclosure coverage: 13.3%**, with 52 candidates in the band 46 / 52 / 65 at name thresholds 0.95 / 0.90 / 0.85.
  Every candidate is published with its evidence.
- **Reviewed every candidate row by hand before trusting the number.** Tiers 1–2 are genuine same-name registrations
  at the hospital's campus. The threshold cuts both ways ("UNIVERSITY OF CHICAGO HOSPITALS" at 0.886–0.895 falls just
  below it), which is why the sensitivity table ships with the figure. The threshold was set before any result was
  seen and was not tuned afterwards.
- **Reproducible figure.** `clear-pricer report` regenerates `docs/results/npi-reconciliation.md` from the gated marts,
  pinned to the input SHA-256s and NPPES files. Two regenerations gave identical SHA-256
  (`735bb9d9…`), and so did a third from the warehouse the *Airflow container* rebuilt (run `m4_reconciliation_1`,
  56/56 gates, parity PASS). The figure reproduces across environments, not just across runs.
- 56/56 dbt nodes green, including 4 new M4 gates and a macro unit-check test (the CMS check-digit example
  `1234567893` passes; flipping its last digit fails). Three reconciliation tables publish to Postgres with parity.

### Decisions (→ CP-DEC 011)
- Two directions; an outcome ladder for resolution; tiered completeness candidates (1 = same address + name,
  2 = same campus + name; 3 and 4 published, not counted); Jaro-Winkler ≥ 0.90 fixed up front and published with
  sensitivity; deterministic SQL macros; rates published, never gated (they're findings, not failures).

### Learnings
- **"Unresolved" was the wrong question at v1 scale; "undisclosed" is the story.** Every NPI the hospitals chose to
  list is clean, and the gap is in what they didn't list. Hospitals disclose 1–5 NPIs while NPPES carries about
  15–23 active hospital registrations under their names on the same campus.
- **Rush's file lists the one address with no NPPES registration.** Its disclosed address (1620 W Harrison) matches
  none of the 23 Rush hospital registrations; exact-address matching alone would have found zero candidates. That's
  why there's a campus (ZIP) tier.
- **The registry has its own data-quality problems:** an individual registered as a Type 2 children's hospital at
  UChicago's address, and hospitals registered under typo'd names ("NORWESTERN", "SROGER", "CHICAGP").
- **NPPES history makes an as-of check possible:** each disclosed NPI's status on the day the hospital published. For
  one NM NPI it's NULL because that NPI's history starts after April. That's honest: the history only knows versions
  from each record's last update on.

### What broke (+ fix)
- **Views over Parquet baked in the builder's absolute path.** The container-built warehouse referenced
  `/opt/clear-pricer/data/...` and broke on the host, and a downloaded warehouse would break the same way. Staging is
  now tables, and the three big staging models are ephemeral (inlined into mart tables). The warehouse is
  self-contained: 12 base tables, 0 views. Rebuilt from scratch to drop the stale views, since dbt doesn't drop
  relations for models that turn ephemeral.
- **`asof` is a DuckDB reserved word** (ASOF joins); the CTE is renamed.
- **Parity SQL broke on a quoted literal:** `'resolved_verified%'` inside the string passed to `postgres_query(...)`.
  Inner quotes are now doubled.
- **A shell heredoc choked on a quote in the SQL**; the dbt files were written directly instead.

### Open / next
- **Milestone 5 (awaiting approval):** the Synthea FHIR R4 path (synthetic only, no PHI), with a mapping report.
- Limitation to state in the case study: NPPES secondary practice locations (`pl_pfile`) aren't modelled, so
  completeness matches only primary practice addresses.

---

## 2026-09-29 — Milestone 5 (synthetic FHIR R4 path)

### What happened
- **Nothing needed from Trevor.** Synthea runs in a Java container (`eclipse-temurin:21-jre`) on the Docker already
  running for Airflow, so there was no local Java install.
- **Pinned the generator before using it.** Synthea **v4.0.0**, the tagged release rather than the moving
  `master-branch-latest`, pinned by jar SHA-256 (`ed43c20a…`). Seed, clinician seed and reference date are fixed.
  **Checked determinism rather than assuming it:** two 3-patient runs with the same arguments were byte-identical in
  content. Only the hospital/practitioner directory *file names* differ, because they embed a wall-clock timestamp.
- **Generated the population:** 200 living patients plus the deceased Synthea adds, 225 patient bundles and 2
  directory bundles, 681 MB, in **22 s**. Regenerated through the new CLI: identical content.
- **Built the parser around measured mapping.** Each resource is wrapped in a read-tracking view, and "mapped" means an
  extractor actually read the leaf. Result: **240,237 resources across 24 types parsed in about 25 s**. 11 types are
  modelled, and the other 13 are published at 0% rather than left out.
- **Validation:**
  - **0 structural issues** (R4 1..1 elements used, coding system+code, date formats).
  - **1,200,521 of 1,200,521 references resolve** (889,570 bundle URNs, 267,345 conditional identifiers, 43,606
    contained).
  - **225 of 225 patients** pass the synthetic-marker gate.
- **The no-PHI pin is now a gate.** `assert_fhir_synthetic_only` fails the run if any patient lacks Synthea's
  identifier system, a 999-range SSN (a range the SSA never issues) or digit-suffixed names. An end-to-end test proves
  a real-looking patient turns the run red. So does a dangling reference. Staging keeps only the marker *flags*: no
  names, SSNs or street lines, even synthetic ones.
- **Code bridge.** Synthea's claim lines are SNOMED (45,368), LOINC, RxNorm, CVX, ICD-10 and CDT. Price files are
  CPT/HCPCS/CDT/MS-DRG/NDC/RC. Only dental CDT is shared: **862 of 69,580 synthetic claim lines (1.2%)** carry a code
  that appears in a real price file (36 codes, 2 hospitals).
- `stage_fhir` joined the DAG: a green Airflow run (`m5_fhir_1`), 78/78 gates, parity PASS. Three FHIR report tables
  publish to Postgres (12 published tables in total). `clear-pricer report` now also regenerates
  `docs/results/fhir-mapping.md`, byte-deterministically.

### Decisions (→ CP-DEC 012)
- Pinned, containerised, byte-deterministic generation; bundles ordered by content hash; mapping measured by
  read-tracking; structural + referential validation (the HL7 FHIR Validator rejected as heavier than the need, and
  it would validate Synthea against profiles it's built to); synthetic-only enforced as a gate; flags, not values,
  for PHI-shaped fields.

### Learnings
- **FHIR "claims" and CMS price files barely share a vocabulary.** Synthetic claims bill clinical concepts (SNOMED);
  hospitals publish billing codes (CPT/HCPCS). Only 1.2% of lines, all dental, could be priced directly. A real
  claims-to-price join would need a SNOMED→CPT crosswalk, which is licensed content and out of scope.
- **ExplanationOfBenefit is 94.7% unmapped**, and that's where the payment detail lives (adjudication categories
  and amounts: 245k + 204k values). The measured mapping report makes it obvious which extension would add the most.
- **One reference resolver isn't enough for FHIR.** The same dataset uses bundle URNs, conditional identifier
  queries into *other* bundles, and `#contained` references.

### What broke (+ fix)
- **43,606 "dangling" references on the first parse.** They were `#coverage` / `#referral` *contained*-resource
  references the resolver didn't know about, not real breaks. Resolved against each resource's `contained` ids, and
  the gate now requires 100%.
- **The clean-clone path broke.** M1's end-to-end test (`clear-pricer run rush`, no Synthea) failed because dbt read
  FHIR staging files that didn't exist. `build` now writes empty FHIR staging tables when none exist, the same pattern
  as the empty NPPES state DB. The try-it path needs no Synthea and no Docker.
- **Practitioner `name` is a list (HumanName), not a string.** It would have staged a Python object, and it's
  PHI-shaped anyway. It's no longer staged.
- **The parser held all 681 MB of bundles in memory** just to sort them by hash. It now hashes to fix the order and
  then reads one bundle at a time.
- **The code bridge double-counted a claim line** whose CDT code appears in both hospitals' files (863 → 862). It now
  counts distinct claim lines, republished.
- **Shell heredocs choked on quotes in generated code** (twice this session); code files are now written directly.

### Open / next
- **Milestone 6 (awaiting approval):** publish + serve. That's the Parquet release (a GitHub Release), stranger-can-
  query docs, and the hosted GitHub Actions schedule (CP-DEC 009). Supabase and FastAPI will need Trevor: a Supabase
  project and credentials, and a decision on anything that costs money.
- Candidate extension: map EOB adjudication (payment detail).

---

## 2026-09-29 — Milestone 6 (publish + serve; repo public)

### What happened
- **Serving shape decided with Trevor (CP-DEC 013):** stay on the Supabase free tier. The full detail ships as a
  Parquet GitHub Release (queryable by DuckDB over HTTPS); Supabase serves the small set (reports + a 49,404-row
  per-code price summary); a FastAPI layer reads the release Parquet. Trevor set up the Supabase project. I reviewed
  his settings (auto-expose off, automatic RLS on, US East region, session-pooler connection) and wrote the
  step-by-step for the credential.
- **Release export:** 13 Parquet files, 116 MB, byte-deterministic (`ORDER BY ALL`, single thread). A manifest pins
  inputs and output hashes, and the fingerprint over both decides whether a release is cut.
- **Supabase publish:** 11 served tables in 5 s, parity passing. Lockdown verified in the catalog: RLS on, one
  read policy, `anon` can `SELECT` but not `INSERT`/`UPDATE`/`DELETE`. Verified over the live REST API too: reads
  return 200, and a `DELETE` with the public key returns **401 permission denied**. The API also survives a
  republish (schema drop and swap) with no dashboard step.
- **Read API** (FastAPI over Parquet): answers from the 117 MB fact with no database behind it. CPT 99213,
  contracted dollars only: Rush median **$185** (30 payer rows, $88–$252) vs UChicago **$61.65**; Northwestern has
  none.
- **CI** (`ci.yml`) went green on its first run: a fresh runner and all tests, including the end-to-end gates, in
  68 s. The M8 clean-clone badge landed early.
- **Hosted pipeline** (`pipeline.yml`, CP-DEC 009): three dispatched runs. The second one went fully green in
  about 25 min: all three hospitals (5 GB NM included), NPPES (history restored from the Actions cache, so every file
  was "already applied — skip"), synthetic FHIR, **80/80 gates**, Supabase publish, export and release.
- **Cross-machine reproducibility, proven.** The GitHub runner (Linux, 4 CPUs) and this workstation (Windows
  Docker, 16 CPUs) independently built release fingerprint **`27a340045c24de0f…`**. Every byte of all 13 files
  matched.
- **Public.** A full-history secret scan came back clean (see below), then the repo went public as Trevor approved.
  **Gate:** a fresh `python:3.12-slim` container (no clone, no login, no credentials) ran the README and QUERY.md
  snippets verbatim, including aggregates over the full 7.37M-row fact straight from the release over HTTPS.

### Decisions (→ CP-DEC 013, 014)
- CP-DEC 013: three read paths (release Parquet / Supabase served set / FastAPI); lockdown re-applied inside every
  schema swap; credentials only from env/.env, with passwords redacted from every driver error; a release is cut
  exactly when the output fingerprint changes; NPPES state lives in the Actions cache with a visible rebuild fallback.
- CP-DEC 014 (corrects CP-DEC 012): "reproducible" now means the same bytes on any machine; Synthea is pinned to one
  CPU; no `any_value` anywhere in the marts.

### Learnings
- **"Deterministic" needs a second machine to mean anything.** Two runs on one machine hid two nondeterminisms
  (`any_value` thread scheduling, and Synthea's multi-threaded generation). Comparing a hosted release to a local
  one with identical inputs exposed both at once, and the diff was diagnosable row by row.
- **Parquet has no 128-bit integer**, so DuckDB writes HUGEINT (e.g. `sum()` of counts) as DOUBLE, and counts
  came back as `8.0`. It's now a release gate.
- **Free-tier Postgres behind a pooler is enough for a public read tier** when the heavy detail lives in object
  storage and gets queried where it sits.

### What broke (+ fix)
- **I leaked a database password into the session.** My first connection test let DuckDB's error message print
  the full DSN, password included, when authentication failed. The failure itself was the password pasted *inside*
  the `[YOUR-PASSWORD]` placeholder brackets. Fix: Trevor rotated the password (with written steps); `secrets.py`
  now redacts DSN passwords from every driver error, and `publish` runs entirely under that guard, with a
  regression test reproducing the exact error. The password never reached git: the history scan is clean.
- **Publish-side lockdown needed care:** a schema swap drops grants and policies, so they're re-applied inside the
  same transaction as the swap.
- **Hosted run 1 failed at Synthea:** the container wrote root-owned output that the runner user couldn't write
  into. It now runs as the calling user on Linux.
- **The first hosted release differed from local in 3 of 13 files.** Row-level diagnosis found `any_value`
  (`agg_code_prices.example_description`), multi-threaded Synthea (one CarePlan activity) and a machine-dependent
  NPPES ledger (idempotency-proof re-applications). All three were fixed, and the cross-machine fingerprint now
  matches. The superseded first release was annotated rather than deleted.
- **The parity check's own SQL broke** twice (duplicate aggregate names, then a quoted literal inside
  `postgres_query`), and the FastAPI test caught DECIMAL money serialising as JSON strings.
- **The Node 20 deprecation warning in CI**: actions bumped to current majors (checkout v7, setup-python v7,
  cache v6).

### Open / next
- **Milestone 7 (awaiting approval):** the written analysis, price variation for the same CPT code across the
  Chicago metro. The data is shaped for it (`agg_code_prices` with `rate_basis`), and NM's thin contracted-dollar
  coverage (4,140 rows) must be stated up front.
- FastAPI isn't deployed to a host yet (needs a hosting decision). Supabase's REST API is the hosted read path
  meanwhile.
- Free-tier pause risk: confirm the daily run keeps the Supabase project active.

---

## 2026-09-29 — Notes pass + Milestone 7 (the analysis)

### What happened
- **Notes pass (asked for by Trevor).** Added an index to this log (one row per milestone: commit, headline number,
  the break worth telling). Refreshed stale README numbers (56 → 80 gates; v1 marked shipped). Saved a project
  memory for the open Supabase free-tier pause check.
- **Measured comparability before writing anything.** List and cash prices exist for 2,961 codes at all three
  hospitals, and "contracted dollars" for 649.
- **The first cut was wrong, and checking it produced the analysis's best material.** It said Rush was the most
  expensive hospital on 398 of 534 codes, with contracted ratios up to 2,428×. That didn't pass a smell test, so
  I checked what each hospital's `rate_basis = 'dollar'` rows actually are:
  - **Rush:** 81,388 `other`-methodology dollars sit at a median 1.00 × list price. 39,116 of them are seven
    Medicare Advantage plans published *at the chargemaster price*.
  - **Northwestern:** its dollars are mostly case rates and per diems (packages, not unit prices). Its 323
    fee-schedule dollars include **$0.01 for a venipuncture listed at $1,361**.
  - **Northwestern also prices 2,124 whole surgical cases as items** carrying a single CPT code. 88300 (a level-I
    pathology exam) showed $67,556 there against $121 at Rush.

  Each became an exclusion rule with a stated reason (CP-DEC 015), and each is also a drift-log entry.
- **Setting vocabularies differ** (Rush and UChicago use `both`), which made the first list-price comparison return
  zero rows. "Outpatient" now includes `both`.
- **Robustness check on the headline:** excluding the case-package items moved the median list-price ratio only
  from 2.21× to 2.11×, and p90 from 5.59× to 4.98×. The residual extremes were "unlisted" catch-all codes, now
  excluded.
- **The findings** (`docs/analysis/price-variation.md`):
  - **List price**, same code, three hospitals: median **2.11×** (p90 4.93×) over 2,323 codes. 55% differ by 2× or
    more. No hospital is uniformly expensive (UChicago highest on 1,275; Rush lowest on 1,153).
  - **Cash:** median **3.38×**, but much of that is policy. Northwestern's cash price is 70% of list, Rush's 50%, and
    **UChicago's is 100% of list on every row** (no cash discount).
  - **Contracted (Rush vs UChicago, fee schedule):** typical gap **1.55×**, with Rush lower on most codes (median
    0.78×). **Within UChicago, the same service varies a median 4.06× across its 6 payers**, so who pays moves
    the price more than where you go. MRI lumbar spine at UChicago runs from $294 (Oscar) to $2,029 (BCBS).
- **Three figures**, light and dark, following the dataviz method:
  - the categorical palette was validated all-pairs in both modes by the script (the light-mode aqua contrast
    warning is covered by legends and a table view beside every chart);
  - every figure was rendered and inspected.

  Inspection caught a clipped legend and dots hiding each other (near-equal prices on a log axis), both fixed.
- **Reproducibility:** the committed page is generated from the public release `data-2026-09-29-27a34004`, and a
  local-export run produced identical results (fingerprint and every number). Tests enforce the method's exclusions
  and byte-identical output.

### Decisions (→ CP-DEC 015)
- The comparable-price definition: CPT Category I, outpatient including `both`, line items only, no unlisted
  codes. List and cash for all three; contracted = fee-schedule dollars, Rush vs UChicago only; a negotiated rate
  equal to list is never contracted. Rejected: volume weighting (no claims data), a composite price index (it would
  bury the payer-spread finding), and showing Northwestern's contracted dollars with a warning.

### Learnings
- **A plausible-looking wrong answer is the most dangerous output of a price analysis.** "Rush is the most
  expensive" came straight out of the data. Only asking *what kind of dollar is this?* exposed MA rates published at
  list price. The `rate_basis` label from M1 was necessary but not sufficient, and methodology matters as much.
- **"Negotiated rate" in these files is a column, not a concept.** Across three files it holds contracted fees,
  packages, percentages of list, list itself, and figures that are probably unit-conversion artifacts.
- **Hospital policy explains cross-hospital cash variation** better than price does: a 100%-of-list cash price is a
  choice, not a cost.

### What broke (+ fix)
- **The trusting first cut**, as above. It became exclusion rules plus an explicit "what kind of dollar is it"
  table at the top of the analysis.
- **The setting mismatch** (`both`) returned an empty comparison until it was normalised.
- **Two counts disagreed by one** (39,355 vs 39,354): two different definitions of "equals list", now one.
- **The narrative crashed on data without the MRI example**, which the synthetic test fixture exposed. The sentence
  is now conditional.
- **Chart review:** the legend clipped "UChicago" and overlapping dots hid a hospital, fixed by short legend labels
  and a small vertical dodge.
- **Shell escaping again:** a heredoc-embedded Python patch silently failed its own assertion, so patches now run
  from files.

### Open / next
- **Milestone 8 (awaiting approval):** `docs/CASE-STUDY.md` (the schema-drift log as its spine) and the final README
  "How it's verified" with real numbers. The clean-clone CI badge already landed in M6.
- Candidate v2 metric: a per-hospital "comparable-price share" (the section 1 table), tracked over releases.
