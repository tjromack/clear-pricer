-- CDC gate: closed versions end no earlier than they start; only the current version is open-ended.
select npi, version, valid_from, valid_to, is_current
from {{ ref('stg_nppes__provider_history') }}
where (valid_to is not null and valid_from is not null and valid_to < valid_from)
   or (is_current and valid_to is not null)
   or (not is_current and valid_to is null)
