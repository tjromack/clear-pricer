-- Per resource type: how many resources, whether they are modelled, how much of their content is mapped, and how
-- many structural issues were found. The one-glance view of the FHIR path.
with r as (select resource_type, count(*) as resources from {{ ref('stg_fhir__resources') }} group by 1),
m as (
    select resource_type, any_value(modelled_type) as modelled,
           count(*) as leaf_paths, count(*) filter (where mapped) as mapped_paths,
           sum(occurrences) as leaf_values, sum(occurrences) filter (where mapped) as mapped_values
    from {{ ref('stg_fhir__mapping') }} group by 1
),
i as (select resource_type, count(*) as issues from {{ ref('stg_fhir__issues') }} group by 1)
select r.resource_type, r.resources, coalesce(m.modelled, false) as modelled, m.leaf_paths,
       coalesce(m.mapped_paths, 0) as mapped_paths,
       round(coalesce(m.mapped_values, 0) / nullif(m.leaf_values, 0), 4) as mapped_value_share,
       coalesce(i.issues, 0) as structural_issues
from r left join m using (resource_type) left join i using (resource_type)
order by r.resources desc, r.resource_type
