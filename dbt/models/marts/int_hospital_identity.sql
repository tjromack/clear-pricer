{{ config(materialized='ephemeral') }}
-- What each price file says about its hospital: names, addresses (as match keys + ZIPs), disclosed Type 2 NPIs.
with f as (select * from {{ ref('stg_hpt__files') }}),
addrs as (
    select hospital_id, a as address,
           regexp_extract(a, '([0-9]{5})(-[0-9]{4})?\s*$', 1) as zip5
    from f, unnest(f.hospital_addresses) as t(a)
),
keys as (
    select hospital_id, list(distinct {{ addr_key('address', 'zip5') }}) as ref_addr_keys,
           list(distinct zip5) as ref_zips
    from addrs group by 1
)
select f.hospital_id, f.hospital_name, f.last_updated_on, f.source_sha256,
       list_distinct(list_transform([f.hospital_name] || f.location_names, n -> {{ norm_name('n') }})) as ref_names,
       k.ref_addr_keys, k.ref_zips,
       f.type_2_npis as disclosed_npis
from f left join keys k using (hospital_id)
