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
