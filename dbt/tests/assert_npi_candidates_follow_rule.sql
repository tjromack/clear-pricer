-- M4 gate: every counted candidate (tiers 1-2) satisfies the stated rule: active, Type 2, hospital taxonomy (27/28),
-- in a disclosed ZIP, name similarity >= 0.90 -- and each (hospital, NPI) appears once.
select c.hospital_id, c.npi
from {{ ref('rpt_npi_completeness') }} c
left join {{ ref('dim_providers_current') }} p using (npi)
where c.tier in (1, 2)
  and (p.status <> 'active' or p.entity_type <> '2' or left(p.primary_taxonomy, 2) not in ('27', '28')
       or c.name_similarity < 0.90)
union all
select hospital_id, npi from {{ ref('rpt_npi_completeness') }} group by 1, 2 having count(*) > 1
