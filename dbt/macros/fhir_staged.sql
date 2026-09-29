{# Read one staged FHIR table: staging/fhir/<name>.parquet (always written, possibly empty). #}
{% macro fhir_staged(name) -%}
read_parquet('{{ var("data_dir") }}/staging/fhir/{{ name }}.parquet')
{%- endmacro %}
