-- Release gate on the published history (half-open intervals, valid_to NULL = current):
--   1. contiguous: a closed version ends exactly where the next version starts;
--   2. no two versions of one NPI are valid on the same day.
-- (1) plus valid_from <= valid_to (assert_nppes_intervals_valid) implies (2); both are checked so the proof doesn't
-- lean on another test. A zero-length version (valid_from = valid_to) is valid on no day, which (2) allows.
with v as (
    select npi, version, valid_from, valid_to,
           lead(valid_from) over (partition by npi order by version) as next_valid_from
    from {{ ref('dim_provider_history') }}
)
select 'gap_or_overlap_between_versions' as problem, npi, version, valid_from, valid_to
from v
where next_valid_from is not null and valid_to is distinct from next_valid_from
union all
select 'two_versions_valid_on_one_day', a.npi, a.version, a.valid_from, a.valid_to
from {{ ref('dim_provider_history') }} a
join {{ ref('dim_provider_history') }} b
  on a.npi = b.npi and a.version < b.version
 and a.valid_from < coalesce(b.valid_to, date '9999-12-31')
 and b.valid_from < coalesce(a.valid_to, date '9999-12-31')
