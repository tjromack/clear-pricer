{{ config(materialized='ephemeral') }}
select * from {{ fhir_staged('references') }}
