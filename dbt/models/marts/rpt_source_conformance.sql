-- What each source file got wrong against the CMS v3 dictionary, and how often. Published, not hidden:
-- this is the per-hospital schema-drift ledger behind docs/schema-drift-log.md.
select
    d.hospital_id,
    f.hospital_name,
    d.source_sha256,
    d.kind,
    d."column",
    d.n,
    round(d.n / nullif(f.records_read, 0), 6) as share_of_records,
    d.first_record,
    d.sample_value
from {{ ref('stg_hpt__drift') }} d
join {{ ref('stg_hpt__files') }} f using (hospital_id, source_sha256)
order by d.hospital_id, d.n desc
