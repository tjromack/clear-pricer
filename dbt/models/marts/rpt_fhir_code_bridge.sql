-- Can a synthetic claim line be priced against a real hospital price file? Only where both sides use the same code
-- system. Synthea bills in SNOMED / LOINC / RxNorm / CVX / CDT / ICD-10; price files bill in CPT / HCPCS / CDT / MS-DRG
-- / NDC / RC. The one shared system is CDT (dental). This table states the overlap as numbers.
with lines as (
    select code_system, count(*) as claim_lines, count(distinct code) as distinct_codes
    from {{ ref('fct_fhir_claim_lines') }} group by 1
),
price_cdt as (select distinct code, hospital_id from {{ ref('dim_charge_codes') }} where code_family = 'CDT'),
cdt as (
    select count(distinct (l.claim_id, l.sequence)) as lines_priceable, count(distinct l.code) as codes_priceable,  -- a line matching 2 hospitals counts once
           count(distinct p.hospital_id) as hospitals
    from {{ ref('fct_fhir_claim_lines') }} l
    join price_cdt p on p.code = upper(l.code)
    where l.code_system = 'http://www.ada.org/cdt'
)
select l.code_system, l.claim_lines, l.distinct_codes,
       case when l.code_system = 'http://www.ada.org/cdt' then 'CDT' end as shared_with_price_files,
       case when l.code_system = 'http://www.ada.org/cdt' then (select lines_priceable from cdt) else 0 end
         as lines_with_a_price_file_code,
       case when l.code_system = 'http://www.ada.org/cdt' then (select codes_priceable from cdt) else 0 end
         as codes_with_a_price_file_code,
       case when l.code_system = 'http://www.ada.org/cdt' then (select hospitals from cdt) else 0 end
         as hospitals_with_those_codes
from lines l
order by l.claim_lines desc
