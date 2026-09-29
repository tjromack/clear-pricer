{{ config(materialized='ephemeral') }}
select * from {{ staged("codes") }}
