-- The headline numbers, per hospital and overall (CP-DEC 011). Candidates = completeness tiers 1-2 only.
with r as (select * from {{ ref('rpt_npi_resolution') }}),
c as (select * from {{ ref('rpt_npi_completeness') }} where tier in (1, 2)),
res as (
    select hospital_id, count(*) as disclosed_npis,
           count(*) filter (where outcome in ('unresolved', 'invalid_npi')) as unresolved_npis,
           count(*) filter (where outcome like 'resolved_verified%') as verified_npis
    from r group by 1
),
comp as (
    select hospital_id,
           count(*) filter (where not disclosed) as undisclosed_candidates,
           count(*) filter (where not disclosed and tier = 1) as undisclosed_tier1,
           count(*) filter (where not disclosed and tier = 2) as undisclosed_tier2
    from c group by 1
),
per as (
    select f.hospital_id,
           coalesce(res.disclosed_npis, 0) as disclosed_npis, coalesce(res.unresolved_npis, 0) as unresolved_npis,
           coalesce(res.verified_npis, 0) as verified_npis,
           coalesce(comp.undisclosed_candidates, 0) as undisclosed_candidates,
           coalesce(comp.undisclosed_tier1, 0) as undisclosed_tier1,
           coalesce(comp.undisclosed_tier2, 0) as undisclosed_tier2
    from {{ ref('stg_hpt__files') }} f
    left join res using (hospital_id) left join comp using (hospital_id)
),
both_ as (
    select * from per
    union all
    select 'ALL', sum(disclosed_npis), sum(unresolved_npis), sum(verified_npis), sum(undisclosed_candidates),
           sum(undisclosed_tier1), sum(undisclosed_tier2) from per
)
select *,
    round(unresolved_npis / nullif(disclosed_npis, 0), 4) as unresolved_rate,
    round(verified_npis / nullif(disclosed_npis, 0), 4) as verified_rate,
    round(disclosed_npis / nullif(disclosed_npis + undisclosed_candidates, 0), 4) as disclosure_coverage
from both_
order by hospital_id = 'ALL', hospital_id
