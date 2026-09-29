-- Payer-specific modifier rules published at file level (JSON modifier_information).
select hospital_id, code, description, setting, payer_name, plan_name, payer_description
from {{ ref('stg_hpt__modifiers') }}
