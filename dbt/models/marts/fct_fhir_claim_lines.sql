-- Synthetic claim lines (Synthea): one row per Claim.item, with the claim's patient, date, and billing provider
-- (resolved through the conditional Organization reference to the synthetic provider directory).
with c as (select * from {{ ref('stg_fhir__claims') }}),
o as (select synthea_id, name from {{ ref('stg_fhir__organizations') }} where resource_type = 'Organization')
select i.claim_id, i.sequence, c.patient_id, c.claim_type, c.created, i.code_system, i.code, i.display, i.net,
       c.total as claim_total, c.currency, o.name as provider_name
from {{ ref('stg_fhir__claim_items') }} i
join c on c.resource_id = i.claim_id
left join o on c.provider_ref = 'Organization?identifier=https://github.com/synthetichealth/synthea|' || o.synthea_id
