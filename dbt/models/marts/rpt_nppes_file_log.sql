-- Every NPPES file the CDC has seen, with what it changed when it was first applied (application order). Later
-- re-applications (idempotency proofs, forced re-runs) change nothing by design and stay in stg_nppes__file_log for audit;
-- publishing them would make the ledger depend on which machine ran which proof.
select * from {{ ref('stg_nppes__file_log') }}
qualify row_number() over (partition by source_file order by seq) = 1
order by seq
