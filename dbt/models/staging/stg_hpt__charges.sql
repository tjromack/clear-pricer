{{ config(materialized='ephemeral') }}
select * from {{ staged("charges") }}
