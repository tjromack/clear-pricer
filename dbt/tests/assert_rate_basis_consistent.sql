-- CP-DEC 006: negotiated_rate is the source dollar amount, present exactly when rate_basis is a dollar basis.
select charge_id, rate_basis, negotiated_rate, negotiated_dollar
from {{ ref('fct_standard_charges') }}
where (rate_basis like 'dollar%') <> (negotiated_rate is not null)
   or negotiated_rate is distinct from (case when rate_basis like 'dollar%' then negotiated_dollar end)
