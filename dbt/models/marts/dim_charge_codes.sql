-- Every billing code on every item (long form; join to fct_standard_charges on item_id), with the declared type kept verbatim and the
-- code_family derived from the code's shape (CPT Cat I/II/III/PLA, HCPCS Level II, CDT) -- CP-DEC 006.
select item_id, hospital_id, code_seq, code, declared_type, code_family, type_conflict
from {{ ref('stg_hpt__codes') }}
