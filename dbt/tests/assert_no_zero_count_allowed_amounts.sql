-- CP-DEC 006: allowed amounts encoded with count "0" describe zero remittances; they must never reach the clean
-- columns (raw values stay in *_raw).
select charge_id
from {{ ref('fct_standard_charges') }}
where count_bucket = '0'
  and (median_amount is not null or p10_amount is not null or p90_amount is not null)
