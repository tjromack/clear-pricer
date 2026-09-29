-- run after every release table is built (the query below reads information_schema, not refs):
-- depends_on: {{ ref('fct_standard_charges') }}
-- depends_on: {{ ref('dim_charge_codes') }}
-- depends_on: {{ ref('agg_code_prices') }}
-- depends_on: {{ ref('rpt_npi_reconciliation') }}
-- depends_on: {{ ref('rpt_npi_resolution') }}
-- depends_on: {{ ref('rpt_npi_completeness') }}
-- depends_on: {{ ref('rpt_source_conformance') }}
-- depends_on: {{ ref('stg_hpt__files') }}
-- depends_on: {{ ref('rpt_nppes_file_log') }}
-- depends_on: {{ ref('dim_modifiers') }}
-- depends_on: {{ ref('rpt_fhir_summary') }}
-- depends_on: {{ ref('rpt_fhir_mapping') }}
-- depends_on: {{ ref('rpt_fhir_code_bridge') }}
-- Release gate: Parquet has no 128-bit integer, so DuckDB would silently write HUGEINT columns as DOUBLE (counts
-- would come back as 8.0). No table that ships in the release may carry a HUGEINT/UHUGEINT column.
select table_name, column_name, data_type
from information_schema.columns
where data_type in ('HUGEINT', 'UHUGEINT')
  and table_schema = 'main'
  and (table_name like 'fct\_%' escape '\' or table_name like 'dim\_%' escape '\'
       or table_name like 'agg\_%' escape '\' or table_name like 'rpt\_%' escape '\'
       or table_name = 'stg_hpt__files')
