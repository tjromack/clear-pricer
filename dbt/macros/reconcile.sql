{# Deterministic, explainable matching helpers for NPI reconciliation (CP-DEC 011). #}

{# Upper-case, & -> AND, punctuation -> space, drop THE/OF/AND/INC/LLC, collapse spaces. #}
{% macro norm_name(x) -%}
trim(regexp_replace(regexp_replace(regexp_replace(regexp_replace(upper({{ x }}), '&', ' AND ', 'g'),
  '[^A-Z0-9 ]', ' ', 'g'), '\b(THE|OF|AND|INC|LLC)\b', ' ', 'g'), ' +', ' ', 'g'))
{%- endmacro %}

{# Address tokens: upper-case alphanumerics. #}
{% macro addr_tokens(addr) -%}
list_filter(string_split(trim(regexp_replace(upper({{ addr }}), '[^A-Z0-9]+', ' ', 'g')), ' '), t -> t <> '')
{%- endmacro %}

{# "<house number> <first non-directional street token> <zip5>", e.g. '251 E. Huron' + 60611 -> '251 HURON 60611'.
   NULL when the address does not start with a house number. #}
{% macro addr_key(addr, zip5) -%}
CASE WHEN regexp_full_match({{ addr_tokens(addr) }}[1], '[0-9]+')
     THEN {{ addr_tokens(addr) }}[1] || ' ' ||
          coalesce(list_filter({{ addr_tokens(addr) }}[2:],
                               t -> t NOT IN ('N', 'S', 'E', 'W', 'NORTH', 'SOUTH', 'EAST', 'WEST'))[1], '')
          || ' ' || {{ zip5 }}
END
{%- endmacro %}

{# NPI check digit: Luhn over '80840' || the 10-digit NPI (the prefix the NPI standard assigns). #}
{% macro npi_luhn_ok(npi) -%}
(regexp_full_match({{ npi }}, '[0-9]{10}') AND
 list_sum(list_transform(range(1, 16), i ->
   CASE WHEN i % 2 = 0
        THEN (2 * CAST(substr(reverse('80840' || {{ npi }}), i, 1) AS INTEGER))
             - CASE WHEN 2 * CAST(substr(reverse('80840' || {{ npi }}), i, 1) AS INTEGER) > 9 THEN 9 ELSE 0 END
        ELSE CAST(substr(reverse('80840' || {{ npi }}), i, 1) AS INTEGER) END)) % 10 = 0)
{%- endmacro %}

{# CMS: report Type 2 NPIs whose primary taxonomy starts '28' (hospital) or '27' (hospital unit). #}
{% macro is_hospital_taxonomy(t) -%}
(left({{ t }}, 2) IN ('27', '28'))
{%- endmacro %}
