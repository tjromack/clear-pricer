# clear-pricer — build log

A running, dated record of noteworthy process, decisions, learnings, and what-broke moments as clear-pricer is built.
**The primary raw material for the case study and any other writing** — kept honest and specific, numbers only when
measured. Per `CLAUDE.md`, keeping this current is part of each milestone's definition of done.

Entry template: **What happened · Decisions · Learnings · What broke (+ fix) · Open/next.**

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
