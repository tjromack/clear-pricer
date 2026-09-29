{{ config(materialized='ephemeral') }}
-- NPPES type-2 history maintained by CDC (clear_pricer/nppes.py) in the attached state DB. Read-only here.
select * from nppes.main.provider_history
