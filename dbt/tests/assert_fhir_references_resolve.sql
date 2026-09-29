-- Referential integrity gate: every FHIR reference resolves -- urn:uuid within its bundle, conditional identifier
-- references against the synthetic provider directory, #id against the resource's contained resources.
select kind, count(*) as unresolved, any_value(target) as example
from {{ ref('stg_fhir__references') }}
where not coalesce(resolved, false)
group by 1
