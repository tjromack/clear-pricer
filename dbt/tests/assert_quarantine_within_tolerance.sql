-- Quality gate: a file whose unmappable (quarantined) rows exceed max_quarantine_share fails the run.
select hospital_id, records_read, quarantined_rows
from {{ ref('stg_hpt__files') }}
where quarantined_rows > {{ var('max_quarantine_share') }} * records_read
