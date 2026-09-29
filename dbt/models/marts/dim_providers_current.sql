-- The registry as of the latest applied NPPES file: one row per NPI (current version), deactivations included.
select npi, entity_type, replacement_npi, org_name, last_name, first_name, middle_name, credential,
       practice_address_1, practice_city, practice_state, practice_postal, practice_country, enumeration_date,
       last_update_date, deactivation_reason, deactivation_date, reactivation_date, primary_taxonomy,
       taxonomy_codes, is_org_subpart, parent_org_name, status, version, valid_from, source_file
from {{ ref('stg_nppes__provider_history') }}
where is_current
