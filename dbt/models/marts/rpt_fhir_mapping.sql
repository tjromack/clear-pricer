-- The FHIR mapping report: every leaf path seen in the synthetic bundles, how often, and whether the parser actually
-- read it ("mapped" is measured by read-tracking, not declared). Unmodelled resource types are listed too.
select resource_type, path, occurrences, mapped, modelled_type
from {{ ref('stg_fhir__mapping') }}
order by resource_type, path
