# Meilisearch index settings

Each tenant owns two indexes, `{schema}__ProductTranslation` and
`{schema}__BlogPostTranslation`. Their settings come from the model's
`MeiliMeta` (`product/models/product.py`, `blog/models/post.py`), are turned
into a payload by `MeiliIndexSettings` (`meili/dataclasses.py`) and
`Client.with_settings` (`meili/_client.py`), and are PATCHed whole by
`IndexMixin.update_meili_settings` (`meili/models.py`). The PreSync hook runs
`meilisearch_apply_settings --all-tenants` on every deploy, so a change to
`MeiliMeta` reaches every store with the next release.

The rule behind every value: **the engine builds a structure for each thing
it is told it may do, so it is told only what a query really does.** The
list of what the queries do is `search/views.py`; when a view starts to filter,
sort or facet on a new field, that field is added to `MeiliMeta` in the same
change.

## Filterable attributes

`MeiliMeta.filterable_fields` names every field a query filters or facets on.
`MeiliMeta.range_fields` names the subset that is also compared. The payload
is the granular form of `filterableAttributes`
([configure granular filters](https://www.meilisearch.com/docs/learn/filtering_and_sorting/configure_granular_filters)):
one entry for the range fields (equality and comparison), one for the rest
(equality only), each with `facetSearch` from `MeiliMeta.facet_search`.

| Index | Equality only | Equality and comparison |
|---|---|---|
| Product | `language_code`, `category`, `brand`, `attribute_values`, `active`, `is_deleted`, `in_stock`, `on_offer` | `effective_price`, `likes_count`, `view_count` |
| Blog | `language_code`, `is_published` | none |

Why each is there: `active`, `is_deleted` and `language_code` scope every
product query (`_visible_products`); `category`, `brand` and `attribute_values`
are the sidebar filters and their facet counts; `in_stock` and `on_offer` are
the two checkboxes; the three range fields are the price and popularity
minimums and maximums and the `facetStats` the sidebar reads as slider bounds.
The blog search filters on `is_published` and the request language and uses
no facets.

Fields that used to be filterable and no query filters by (`name`,
`category_name`, `stock`, `discount_percent`, `master_id`, `attributes`, and the
blog's `title`, `likes_count`, `category`, `category_name`, `master_id`) are
still in the documents, through their searchable, displayed or
additional-field entry, and are read back in results. They are not indexed for
filtering. A filter on one of them, `CONTAINS` included, is rejected by the
engine until the field is added to `filterable_fields`.

`range_fields` must be a subset of `filterable_fields`;
`filterable_attributes_payload` raises otherwise.

## Facet search

`MeiliMeta.facet_search = False` on both indexes. It is sent twice: as the
index-wide `facetSearch` setting and inside every granular entry. The
facet-search endpoint (searching within a facet's values) is called nowhere,
and the database behind it costs indexing time and disk for every facet value.
Turn it on in the model that starts to call the endpoint.

## Sortable attributes

Product: `likes_count`, `effective_price`, `view_count`, `created_at`, the
four `sort` values of the product search. `discount_percent` and `stock` are
not sortable: `discount_percent` ranks through the custom ranking rule
`discount_percent:desc`, which needs no sortable entry. Blog: `likes_count`,
`view_count`, `created_at`.

## Faceting and pagination

| Setting | Product | Blog | Why |
|---|---|---|---|
| `faceting.maxValuesPerFacet` | 500 | 100 | `attribute_values` holds the value ids of every attribute in one facet, returned in lexicographic order and cut at this cap. A cap below the number of distinct values drops ids silently (a cap of 20 did on the demo store; 95 distinct values already sat against 100). Do not lower it. The blog has no facets. |
| `pagination.maxTotalHits` | 20000 | 20000 | The engine counts every hit up to this cap on each query, and past about 20000 a query can take seconds ([indexing best practices](https://www.meilisearch.com/docs/learn/indexing/indexing_best_practices)). No listing pages that deep. A product used to allow five million and the blog, in the code, 50000. |
| `searchCutoffMs` | 1500 | 1500 | Unchanged. |

A total above `maxTotalHits` is reported as the cap, so `estimatedTotalHits`
of a very large catalogue stops at 20000.

## What a settings change costs on deploy

| Changed | Effect on each tenant index |
|---|---|
| `filterableAttributes` (any entry) or `sortableAttributes` | The engine reindexes the index's documents. The settings task stays enqueued until it is done. |
| `facetSearch`, `faceting`, `pagination`, `searchCutoffMs` | Applied at once, no reindex. |

`update_meili_settings` awaits each task for
`MEILISEARCH["SETTINGS_TASK_TIMEOUT_MS"]` (env `MEILI_SETTINGS_TASK_TIMEOUT_MS`,
default 600000). The SDK's own default of five seconds made a reindex longer
than that raise, after which the PreSync hook printed "DRIFTED" for a change
the engine went on to apply. A task that outlasts the configured time still
fails the command for that tenant (exit 1); the engine finishes it regardless.

## Inspecting an index

`meilisearch_inspect_index` prints each granular filter entry with its
operators, the sortable fields, `facetSearch`, `maxValuesPerFacet` and
`maxTotalHits`; `--show-settings` prints the raw settings.

## Tests

- `tests/unit/meili/test_granular_filterable.py`: the payload builder, the two
  models' exact settings, and that every filterable and sortable field is still
  a field of the indexed document.
- `tests/integration/search/test_index_settings_live.py`: the settings the live
  engine reports equal the ones sent, and applying them twice changes nothing.
