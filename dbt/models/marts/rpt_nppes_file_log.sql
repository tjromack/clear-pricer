-- Every NPPES file the CDC has seen, with what it changed when it was first applied (application order). Later
-- re-applications (idempotency proofs, forced re-runs) change nothing by design and stay in stg_nppes__file_log for audit;
-- publishing them would make the ledger depend on which machine ran which proof.
-- seq is renumbered 1..n over the published rows: the raw counter counts those re-applications too, so on a machine
-- that ran a proof it has gaps (found 2026-10-07: the same ledger published 1,2,3,4,5 on the runner and 1,2,3,4,9 on
-- a workstation, the only byte difference between the two releases).
with first_application as (
    select * from {{ ref('stg_nppes__file_log') }}
    qualify row_number() over (partition by source_file order by seq) = 1
)
select (row_number() over (order by seq))::integer as seq, * exclude (seq)
from first_application
order by seq
