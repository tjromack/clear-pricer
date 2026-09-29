-- CDC gate: every NPI has exactly one current version, versions are 1..n with no gaps or duplicates.
select npi, count(*) filter (where is_current) as current_versions, count(*) as versions, max(version) as max_version,
       count(distinct version) as distinct_versions
from {{ ref('stg_nppes__provider_history') }}
group by npi
having count(*) filter (where is_current) <> 1 or max(version) <> count(*) or count(distinct version) <> count(*)
