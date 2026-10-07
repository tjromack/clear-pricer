-- Header vs lines on the synthetic claims, published as a source finding (CP-DEC 021). Synthea's Claim.total does not
-- equal the sum of its item.net values: pharmacy items carry no net at all, and on priced claims the header sits above
-- the lines on some claims and below on others. The pipeline's own joins are gated separately
-- (assert_fhir_claims_match_eobs); this table measures the source. Money is compared in integer cents.
with lines as (
    select claim_id, count(*) as lines, count(net) as priced_lines,
           sum(round(net * 100))::bigint as lines_cents
    from {{ ref('stg_fhir__claim_items') }}
    group by 1
),
c as (
    select h.claim_type, round(h.total * 100)::bigint as header_cents, l.lines, l.priced_lines, l.lines_cents
    from {{ ref('stg_fhir__claims') }} h
    left join lines l on l.claim_id = h.resource_id
)
select
    claim_type,
    count(*) as claims,
    coalesce(sum(lines), 0)::bigint as lines,
    coalesce(sum(priced_lines), 0)::bigint as priced_lines,
    count(*) filter (where coalesce(priced_lines, 0) = 0) as claims_without_priced_lines,
    count(*) filter (where priced_lines > 0 and header_cents = lines_cents) as header_equals_lines,
    count(*) filter (where priced_lines > 0 and header_cents > lines_cents) as header_above_lines,
    count(*) filter (where priced_lines > 0 and header_cents < lines_cents) as header_below_lines,
    coalesce(sum(header_cents), 0)::bigint as header_cents,
    coalesce(sum(lines_cents), 0)::bigint as lines_cents
from c
group by 1
order by 1
