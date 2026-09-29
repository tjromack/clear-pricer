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

---
*Next entry = CP-DEC 006.*
