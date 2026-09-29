-- Schema gate: an NPPES file missing a projected column was rejected (not loaded) -> the run fails until handled.
select seq, source_file, kind from {{ ref('stg_nppes__file_log') }} where outcome = 'rejected_schema'
