-- The NPPES SCD2 history, materialised so it ships in the release (CP-DEC 019). One row per NPI per version, all
-- entity types. Intervals are half-open: a version is valid on day d when valid_from <= d < valid_to (valid_to NULL =
-- still current). Gated: one current version per NPI, versions 1..n, contiguous, no two versions valid on one day.
select * from {{ ref('stg_nppes__provider_history') }}
