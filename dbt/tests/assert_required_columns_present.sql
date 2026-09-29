-- Schema gate: a file missing a CMS-required header (row 1 or row 3) cannot be mapped faithfully -> fail.
-- Unknown *extra* columns do not fail: they are hospital-created elements the spec allows; they are logged.
select hospital_id, kind, "column"
from {{ ref('stg_hpt__drift') }}
where kind in ('missing_required_column', 'missing_required_meta', 'truncated_header')
