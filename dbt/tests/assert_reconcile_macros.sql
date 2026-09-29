-- Unit checks for the matching macros on known inputs; any returned row is a failure.
-- 1234567893 is the check-digit example from the CMS NPI standard; flipping its last digit must fail.
with cases as (
    select 'luhn valid example' as name, {{ npi_luhn_ok("'1234567893'") }} = true as ok
    union all select 'luhn wrong check digit', {{ npi_luhn_ok("'1234567890'") }} = false
    union all select 'luhn non-numeric', {{ npi_luhn_ok("'12345678AB'") }} = false
    union all select 'luhn real disclosed NPI (Rush)', {{ npi_luhn_ok("'1932213600'") }} = true
    union all select 'addr key strips direction + punctuation',
        {{ addr_key("'251 E. Huron, Chicago, IL 60611'", "'60611'") }} = '251 HURON 60611'
    union all select 'addr key matches NPPES spelling',
        {{ addr_key("'251 E HURON ST # F5-704'", "'60611'") }} = '251 HURON 60611'
    union all select 'addr key spelled-out direction',
        {{ addr_key("'5841 South Maryland, Chicago, IL 60637'", "'60637'") }} = '5841 MARYLAND 60637'
    union all select 'addr key needs a house number',
        {{ addr_key("'JOHN H STROGER HOSPITAL 1901 W HARRISON ST'", "'60612'") }} is null
    union all select 'name normalisation',
        {{ norm_name("'The University of Chicago Medical Center'") }} = 'UNIVERSITY CHICAGO MEDICAL CENTER'
    union all select 'name ampersand',
        {{ norm_name("'RUSH UNIVERSITY &MEDICAL CENTER'") }} = 'RUSH UNIVERSITY MEDICAL CENTER'
    union all select 'hospital taxonomy', {{ is_hospital_taxonomy("'282N00000X'") }}
        and {{ is_hospital_taxonomy("'273R00000X'") }} and not {{ is_hospital_taxonomy("'261QM1300X'") }}
)
select name from cases where not coalesce(ok, false)
