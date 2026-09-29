{# Read one staged Parquet table across every hospital: staging/hpt/<hospital>/<name>.parquet #}
{% macro staged(name) -%}
read_parquet('{{ var("data_dir") }}/staging/hpt/*/{{ name }}.parquet', union_by_name = true)
{%- endmacro %}
