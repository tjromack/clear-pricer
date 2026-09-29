-- Price summary per hospital x billing code x setting x rate basis: the served-tier view of the 7.37M-row fact
-- (CP-DEC 013). Codes are CPT / HCPCS Level II / CDT by derived code_family (CP-DEC 006), plus MS-DRG. rate_basis is
-- kept as a grouping key so a consumer can compare contracted dollars only, rather than mixing in dollars derived from a
-- percentage of the chargemaster price.
with codes as (
    select distinct item_id, code, coalesce(code_family, declared_type) as code_family
    from {{ ref('dim_charge_codes') }}
    where code_family in ('CPT_CAT_I', 'CPT_CAT_II', 'CPT_CAT_III', 'CPT_PLA', 'CPT_MAAA', 'HCPCS_II', 'CDT')
       or declared_type = 'MS-DRG'
)
select
    f.hospital_id, c.code_family, c.code, coalesce(f.setting, 'unspecified') as setting, f.rate_basis,
    count(*) as charge_rows,
    count(distinct f.payer_name || '|' || f.plan_name) as payer_plans,
    min(f.negotiated_rate) as rate_min,
    quantile_cont(f.negotiated_rate, 0.25) as rate_p25,
    median(f.negotiated_rate) as rate_median,
    quantile_cont(f.negotiated_rate, 0.75) as rate_p75,
    max(f.negotiated_rate) as rate_max,
    median(f.gross_charge) as gross_median,
    median(f.discounted_cash) as cash_median,
    any_value(f.description) as example_description
from {{ ref('fct_standard_charges') }} f
join codes c using (item_id)
group by all
