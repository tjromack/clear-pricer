-- M4 gate: every NPI a price file disclosed gets exactly one resolution row -- none dropped, none duplicated.
with expected as (
    select hospital_id, npi from {{ ref('stg_hpt__files') }}, unnest(type_2_npis) as t(npi)
),
got as (select hospital_id, npi, count(*) as n from {{ ref('rpt_npi_resolution') }} group by 1, 2)
select coalesce(e.hospital_id, g.hospital_id) as hospital_id, coalesce(e.npi, g.npi) as npi, g.n
from expected e full join got g using (hospital_id, npi)
where g.n is null or g.n <> 1 or e.npi is null
