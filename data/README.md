# data/ — gitignored on purpose

Nothing in `data/` is committed. Raw hospital price files and the NPPES registry are large (hundreds of MB to GBs),
and DB volumes and the published Parquet don't belong in git either.

Layout (created by the pipeline, all gitignored):

```
data/
  raw/          # downloaded CMS price files + NPPES dumps (fetched via curl; proxy-safe)
  staging/      # parsed/cleaned intermediates
  warehouse/    # DuckDB file(s)
  published/    # the clean Parquet release (ships via a GitHub Release, not the repo)
```

To populate it: run the ingestion step (see `TODO.md` / the pipeline docs). Every source is **public or synthetic** —
no PHI, no client data. Small, safe **fixtures** for tests live in `tests/fixtures/`, not here, and the human-readable
**schema-drift log** lives in `docs/schema-drift-log.md`.
