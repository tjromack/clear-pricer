-- Release gate: the published ledger's seq is 1..n with no gaps, so it is the same on every machine that applied the
-- same files (a raw counter would leak machine-local proof runs into the release).
select count(*) as n, min(seq) as min_seq, max(seq) as max_seq
from {{ ref('rpt_nppes_file_log') }}
having count(*) > 0 and (min(seq) <> 1 or max(seq) <> count(*))
