-- Integrity gate: every data record read is either published or quarantined, and every published row
-- reaches the mart. A mismatch means the pipeline dropped rows silently (design pin 3). Also fails an empty file.
with published as (
    select hospital_id, count(*) as n from {{ ref('fct_standard_charges') }} group by 1
)
select f.hospital_id, f.records_read, f.charge_rows, f.quarantined_rows, coalesce(p.n, 0) as published_rows
from {{ ref('stg_hpt__files') }} f
left join published p using (hospital_id)
where f.records_read = 0
   or f.records_read <> f.charge_rows + f.quarantined_rows
   or f.charge_rows <> coalesce(p.n, 0)
