-- Header vs lines, both directions: every agg_code_prices row (the header) must be reproduced by the charge lines it
-- summarises, and every qualifying charge line must land in a header row. Counts compare exactly; money (min / max
-- rate) compares with a half-cent tolerance, never exactly. Returns rows only on mismatch.
-- The lines are charge x code pairs, not charges: one charge with a CPT and a HCPCS code is a line under each, which
-- is why sum(charge_rows) exceeds the distinct charges it covers (docs/grain.md).
with codes as (
    select distinct item_id, code, coalesce(code_family, declared_type) as code_family
    from {{ ref('dim_charge_codes') }}
    where code_family in ('CPT_CAT_I', 'CPT_CAT_II', 'CPT_CAT_III', 'CPT_PLA', 'CPT_MAAA', 'HCPCS_II', 'CDT')
       or declared_type = 'MS-DRG'
),
lines as (
    select f.hospital_id, c.code_family, c.code, coalesce(f.setting, 'unspecified') as setting, f.rate_basis,
           count(*) as charge_rows, min(f.negotiated_rate) as rate_min, max(f.negotiated_rate) as rate_max
    from {{ ref('fct_standard_charges') }} f
    join codes c using (item_id)
    group by all
),
header as (
    select hospital_id, code_family, code, setting, rate_basis, charge_rows, rate_min, rate_max
    from {{ ref('agg_code_prices') }}
)
select coalesce(h.hospital_id, l.hospital_id) as hospital_id, coalesce(h.code, l.code) as code,
       coalesce(h.code_family, l.code_family) as code_family, coalesce(h.setting, l.setting) as setting,
       coalesce(h.rate_basis, l.rate_basis) as rate_basis,
       h.charge_rows as header_rows, l.charge_rows as line_rows,
       case when h.hospital_id is null then 'lines_without_header'
            when l.hospital_id is null then 'header_without_lines'
            when h.charge_rows <> l.charge_rows then 'row_count_mismatch'
            else 'rate_mismatch' end as problem
from header h
full outer join lines l
  on h.hospital_id = l.hospital_id and h.code_family = l.code_family and h.code = l.code
 and h.setting = l.setting and h.rate_basis = l.rate_basis
where h.hospital_id is null or l.hospital_id is null
   or h.charge_rows <> l.charge_rows
   or abs(coalesce(h.rate_min, 0) - coalesce(l.rate_min, 0)) > 0.005
   or abs(coalesce(h.rate_max, 0) - coalesce(l.rate_max, 0)) > 0.005
   or (h.rate_min is null) <> (l.rate_min is null)
