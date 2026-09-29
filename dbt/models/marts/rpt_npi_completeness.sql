-- Direction 2 (NPPES -> price file): active hospital-taxonomy Type 2 NPIs registered under the hospital's name at its
-- disclosed addresses (tier 1) or campus ZIP (tier 2), flagged by whether the price file listed them. CMS requires the
-- MRF to list the Type 2 NPIs of the hospital and all its locations; undisclosed rows are *candidates* -- NPPES
-- registrations are rarely retired, so a candidate may be a stale or billing-only registration. Tier 3 (same address,
-- different name) and tier 4 (near-miss name, similarity 0.80-0.90, same campus) are published for transparency and for
-- the threshold sensitivity table; neither is counted.
with h as (select * from {{ ref('int_hospital_identity') }}),
p as (
    select *, {{ norm_name('org_name') }} as norm_org, {{ norm_name('parent_org_name') }} as norm_parent,
           left(practice_postal, 5) as zip5,
           {{ addr_key('practice_address_1', 'left(practice_postal, 5)') }} as practice_addr_key
    from {{ ref('dim_providers_current') }}
    where status = 'active' and entity_type = '2' and {{ is_hospital_taxonomy('primary_taxonomy') }}
),
m as (
    select h.hospital_id, p.npi, p.org_name, p.parent_org_name, p.primary_taxonomy, p.practice_address_1,
           p.zip5, p.is_org_subpart, p.enumeration_date, p.last_update_date,
           greatest(list_max(list_transform(h.ref_names, r -> jaro_winkler_similarity(p.norm_org, r))),
                    coalesce(list_max(list_transform(h.ref_names, r -> jaro_winkler_similarity(p.norm_parent, r))), 0))
             as name_similarity,
           coalesce(list_contains(h.ref_addr_keys, p.practice_addr_key), false) as address_match,
           list_contains(h.disclosed_npis, p.npi) as disclosed
    from h join p on list_contains(h.ref_zips, p.zip5)
)
select *,
    case when address_match and name_similarity >= 0.90 then 1
         when name_similarity >= 0.90 then 2
         when address_match then 3
         else 4 end as tier
from m
where address_match or name_similarity >= 0.80
order by hospital_id, tier, npi
