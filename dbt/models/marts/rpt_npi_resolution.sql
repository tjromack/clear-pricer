-- Direction 1 (price file -> NPPES): does every NPI a hospital disclosed resolve to the right provider?
with h as (select * from {{ ref('int_hospital_identity') }}),
d as (select h.*, npi from h, unnest(h.disclosed_npis) as t(npi)),
p as (select * from {{ ref('dim_providers_current') }}),
hist as (select npi, status, valid_from, valid_to from {{ ref('stg_nppes__provider_history') }}),
status_asof as (  -- status on the date the hospital published its file (SCD2 as-of join; NULL = history starts later)
    select d.hospital_id, d.npi, min(x.status) as status_as_of_file_date  -- intervals do not overlap: one match at most
    from d join hist x on x.npi = d.npi
     and try_cast(d.last_updated_on as date) >= x.valid_from
     and (x.valid_to is null or try_cast(d.last_updated_on as date) < x.valid_to)
    group by 1, 2
),
j as (
    select d.hospital_id, d.hospital_name, d.npi, d.last_updated_on as file_date,
           regexp_full_match(d.npi, '[0-9]{10}') as format_ok,
           {{ npi_luhn_ok('d.npi') }} as check_digit_ok,
           p.npi is not null as in_nppes,
           p.status, p.entity_type, p.org_name as nppes_name, p.primary_taxonomy,
           coalesce({{ is_hospital_taxonomy('p.primary_taxonomy') }}, false) as hospital_taxonomy,
           p.practice_address_1 as nppes_address, left(p.practice_postal, 5) as nppes_zip5,
           list_max(list_transform(d.ref_names, r -> jaro_winkler_similarity({{ norm_name('p.org_name') }}, r)))
             as name_similarity,
           coalesce(list_contains(d.ref_addr_keys,
                    {{ addr_key('p.practice_address_1', 'left(p.practice_postal, 5)') }}), false) as address_match,
           coalesce(list_contains(d.ref_zips, left(p.practice_postal, 5)), false) as zip_match,
           a.status_as_of_file_date
    from d
    left join p on p.npi = d.npi
    left join status_asof a on a.hospital_id = d.hospital_id and a.npi = d.npi
)
select *,
    coalesce(name_similarity >= 0.90, false) as name_match,
    case
        when not format_ok or not check_digit_ok then 'invalid_npi'
        when not in_nppes then 'unresolved'
        when status <> 'active' then 'resolved_inactive'
        when entity_type <> '2' then 'resolved_not_organization'
        when not hospital_taxonomy then 'resolved_not_hospital_taxonomy'
        when name_similarity < 0.90 or not zip_match then 'resolved_identity_mismatch'
        when not address_match then 'resolved_verified_campus'  -- right hospital + campus ZIP, different street
        else 'resolved_verified'
    end as outcome
from j
order by hospital_id, npi
