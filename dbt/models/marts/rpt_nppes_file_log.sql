-- Every NPPES file the CDC has seen, in application order, with what it changed. The proof that a delta is a delta.
select * from {{ ref('stg_nppes__file_log') }} order by seq
