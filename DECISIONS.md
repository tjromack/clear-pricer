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

---
*Next entry = CP-DEC 005.*
