-- M4 gate: the headline row adds up -- ALL equals the sum of hospitals, and no count exceeds what it is a share of.
with per as (select * from {{ ref('rpt_npi_reconciliation') }} where hospital_id <> 'ALL'),
tot as (select * from {{ ref('rpt_npi_reconciliation') }} where hospital_id = 'ALL')
select 'all_row' as problem from tot, (select sum(disclosed_npis) d, sum(unresolved_npis) u, sum(verified_npis) v,
                                              sum(undisclosed_candidates) c from per) s
where tot.disclosed_npis <> s.d or tot.unresolved_npis <> s.u or tot.verified_npis <> s.v
   or tot.undisclosed_candidates <> s.c
union all
select 'bounds' from {{ ref('rpt_npi_reconciliation') }}
where unresolved_npis + verified_npis > disclosed_npis
   or undisclosed_tier1 + undisclosed_tier2 <> undisclosed_candidates
