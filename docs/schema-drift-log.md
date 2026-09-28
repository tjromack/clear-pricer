# Schema-drift log

The record of every time an upstream source (a CMS hospital price file, an NPPES dump) deviated from the expected
schema — **what changed, when it was observed, and how the pipeline handled it** (mapped, quarantined, or logged as
unmappable — never silently dropped). This log is the spine of the case study and is written *as drift is encountered*,
not reconstructed afterwards.

Entry template:

| Date observed | Source | What drifted | How the pipeline handled it | Follow-up |
|---|---|---|---|---|

_(No entries yet — populated from Milestone 1 onward, once real files are parsed.)_
