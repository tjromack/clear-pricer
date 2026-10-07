-- Grain gate: the declared key identifies exactly one row. GROUP BY treats NULLs as equal, so a key component that is
-- NULL on two rows still counts as a duplicate. Every release table carries one (docs/grain.md, CP-DEC 020).
{% test grain(model, key) %}
select {{ key | join(', ') }}, count(*) as n
from {{ model }}
group by {{ key | join(', ') }}
having count(*) > 1
{% endtest %}
