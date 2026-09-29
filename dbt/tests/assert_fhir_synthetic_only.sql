-- Design pin 1 as a gate: no PHI, by construction. Every Patient must carry all of Synthea's markers -- its identifier
-- system, an SSN (if any) in the 999 range the SSA never issues, and digit-suffixed names. One real-looking patient
-- fails the run.
select resource_id, has_synthea_identifier, ssn_in_999_range, names_digit_suffixed
from {{ ref('stg_fhir__patients') }}
where not coalesce(has_synthea_identifier, false)
   or ssn_in_999_range is false
   or not coalesce(names_digit_suffixed, false)
