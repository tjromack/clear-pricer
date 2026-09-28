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
