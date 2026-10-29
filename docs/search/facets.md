# Product search facets: counts and ranges

`GET /api/v1/search/product` (`search/views.py: product_meili_search`) can
answer a whole filter sidebar in one request. The `facets` parameter lists
the facet fields to return (`category`, `brand`, `attribute_values`,
`effective_price`, `likes_count`, `view_count`, `price_range`,
`language_code`); the response carries `facet_distribution` (counts per
key) and `facet_stats` (`min`/`max` of a numeric facet).

Without `sidebarFacets`, every facet is what the engine reports for the
request's own matches: the query and every filter narrow it. Only
`attribute_values` is already counted the sidebar way (below).

## Disjunctive counts

A sidebar that narrowed its own counts would hide the alternatives of a
pick: ticking one brand would show every other brand as 0. The standard
fix is to count a filter's own values WITHOUT that filter's clause while
every other filter still narrows them. Meilisearch has no single-request
option for it; its documented recipe is a `multi-search` with the main
query plus one count-only query per filter, that filter's clause left out.

`meili/querysets.py` does this for a `FacetGroup`:

- `IndexQuerySet.filter_facet_group(facet, selected, members)` adds an
  `facet IN [...]` clause and records the group. When the facet is also in
  `set_facets`, `search()` sends the group's count query (`limit` 0, only
  that facet, the same `q`, matching strategy and locales).
- `members` is every facet key the group governs. Only the group's
  UNSELECTED members are read from its count query; a selected key keeps
  the main query's count (already its disjunctive count, and it stays
  listed when the engine's `maxValuesPerFacet` cap pushes it out of the
  larger count query). A sibling the cap omits is simply not listed.
- `members=None` makes the group the WHOLE facet field: every key of the
  count query is listed, so the result equals a request without that
  clause, `maxValuesPerFacet` cap included. The category and brand filters
  are groups of this kind.

`attribute_values` always uses groups, one per attribute
(`product/attribute_selection.py`). `category` and `brand` do so only with
`sidebarFacets=true`; without it a request keeps today's narrowed counts.

## `sidebarFacets`

`sidebarFacets=true` (with `scopeCategory` optionally) turns the facets
the request names into the sidebar's:

| Facet | Counted over |
|---|---|
| `category` | the query and every filter except `categories` (the scope category's own clause included) |
| `brand` | the query and every filter except `brands` |
| `attribute_values` | the query and every filter except the attribute's own values (as always) |
| `effective_price` stats | the active products of the language in `scopeCategory`'s subtree, or all of them; no query, no filter |
| `likes_count`, `view_count` stats | the active products of the language; no query, no filter, no scope |

`categories` stays a filter of the listing: the page's category and the
shopper's picks together. `scopeCategory` is not: it only scopes the price
range, so the slider keeps the category's own bounds while filters change.

The three ranges are `StatsBaseline`s (`IndexQuerySet.stats_over`): extra
queries of the same `multi-search` with `q` empty and only the baseline's
filters. The facets they cover are taken out of the main query, so the
response does not also carry their filtered stats or distributions.

The relaxed-query retry (a multi-word query that finds nothing is retried
without its first word) re-runs the whole `multi-search`, so the counts
always describe the query the listing shows.

The equality of each count with the separate request the storefront used to
make is pinned by `tests/integration/search/test_product_search_sidebar_facets.py`
(live engine, 14 filter combinations times two scopes).

## Facet reads are not searches

A request that asks for facets and for at most one product
(`_COUNT_ONLY_MAX_LIMIT` in `search/views.py`) is a facet read: the
shopper sees nothing to click. `_mark_count_only` flags it on the request
and `SearchAnalyticsMiddleware` does not record a `SearchQuery` for it. A
request for results, with or without facets, is a search.
