-- Header vs lines on the synthetic claims, the parts this pipeline owns (returns rows only on mismatch):
--   lines -> header: every claim item belongs to a parsed claim;   header -> lines: every claim has at least one item;
--   claim <-> EOB: exactly one EOB per claim, none orphaned, and EOB.total equals Claim.total within half a cent.
-- The Synthea input is pinned (version, jar hash, seed, end date -- CP-DEC 012, 018), so a mismatch here can only come
-- from parsing. Synthea's own total-vs-item.net disagreement is a source property, measured in rpt_fhir_claim_totals.
with items as (select claim_id, count(*) as n from {{ ref('stg_fhir__claim_items') }} group by 1),
eobs as (select claim_id, count(*) as n, min(total_amount) as total_amount
         from {{ ref('stg_fhir__eobs') }} group by 1)
select 'item_without_claim' as problem, i.claim_id
from items i left join {{ ref('stg_fhir__claims') }} c on c.resource_id = i.claim_id
where c.resource_id is null
union all
select 'claim_without_items', c.resource_id
from {{ ref('stg_fhir__claims') }} c left join items i on i.claim_id = c.resource_id
where i.claim_id is null
union all
select 'eob_without_claim', e.claim_id
from eobs e left join {{ ref('stg_fhir__claims') }} c on c.resource_id = e.claim_id
where c.resource_id is null
union all
select case when e.claim_id is null then 'claim_without_eob'
            when e.n > 1 then 'claim_with_several_eobs'
            else 'eob_total_differs' end, c.resource_id
from {{ ref('stg_fhir__claims') }} c left join eobs e on e.claim_id = c.resource_id
where e.claim_id is null or e.n > 1
   or abs(coalesce(e.total_amount, 0) - coalesce(c.total, 0)) > 0.005
   or (e.total_amount is null) <> (c.total is null)
