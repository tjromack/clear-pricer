-- Integrity gate: an NPI is exactly 10 digits.
select npi from {{ ref('stg_nppes__provider_history') }} where not regexp_full_match(npi, '[0-9]{10}')
