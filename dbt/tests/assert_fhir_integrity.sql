-- Integrity gates for the FHIR path:
--  1. no entry the parser could not treat as a resource (non-Bundle file, entry without resource, resource without id);
--  2. every modelled resource reached its table (none silently dropped);
--  3. (resource_type, id) is unique across the dataset.
-- Other structural findings (missing R4 1..1 elements, codings without system/code, bad dates) are published in
-- stg_fhir__issues / rpt_fhir_summary as conformance findings, not failures.
select 'integrity_issue:' || kind as problem, count(*) as n
from {{ ref('stg_fhir__issues') }}
where kind in ('not_a_bundle', 'entry_without_resource', 'missing_id')
group by 1
union all
select 'rows_lost:' || r.resource_type, abs(r.n - coalesce(t.n, 0))
from (select resource_type, count(*) as n from {{ ref('stg_fhir__resources') }}
      where resource_type in ('Patient', 'Encounter', 'Condition', 'Observation', 'Procedure', 'MedicationRequest',
                              'Claim', 'ExplanationOfBenefit', 'Organization', 'Location', 'Practitioner')
      group by 1) r
left join (
    select 'Patient' as resource_type, count(*) as n from {{ ref('stg_fhir__patients') }}
    union all select 'Encounter', count(*) from {{ ref('stg_fhir__encounters') }}
    union all select 'Condition', count(*) from {{ ref('stg_fhir__conditions') }}
    union all select 'Observation', count(*) from {{ ref('stg_fhir__observations') }}
    union all select 'Procedure', count(*) from {{ ref('stg_fhir__procedures') }}
    union all select 'MedicationRequest', count(*) from {{ ref('stg_fhir__medication_requests') }}
    union all select 'Claim', count(*) from {{ ref('stg_fhir__claims') }}
    union all select 'ExplanationOfBenefit', count(*) from {{ ref('stg_fhir__eobs') }}
    union all select resource_type, count(*) from {{ ref('stg_fhir__organizations') }} group by 1
) t using (resource_type)
where r.n <> coalesce(t.n, 0)
union all
select 'duplicate_id:' || resource_type, count(*)
from (select resource_type, resource_id from {{ ref('stg_fhir__resources') }}
      group by 1, 2 having count(*) > 1)
group by resource_type
