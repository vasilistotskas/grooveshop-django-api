# Search API

Meilisearch-backed search over products and blog posts, plus click
attribution, trending queries and per-store analytics.

**Source of truth:** `search/views.py`, `search/serializers.py`,
`search/middleware.py`, `meili/querysets.py`, and the generated
`schema.yml`. Where this page and the code disagree, the code wins.

## Conventions

- **Response bodies are camelCase.** `CamelCaseJSONRenderer` is the global
  default renderer, so a serializer field `estimated_total_hits` goes out as
  `estimatedTotalHits`. A leading underscore is mangled by the renderer
  (`_formatted` → `formatted`, `_rankingScore` → `rankingScore`), which is
  why the federated view renames `_federation` to `federation` explicitly.
- **Query parameters accept either spelling.** `CamelCaseMiddleWare`
  underscoreizes `request.GET`, so `languageCode` and `language_code` both
  reach the view. The schema advertises the camelCase form.
- **Base URL** is `/api/v1` on the store's own API host. Never hardcode a
  merchant's domain; `webside.gr` is tenant #1's, not a platform default.
- Languages: `el` (default), `en`, `de`.

## Endpoints

| Endpoint | Method | Permission |
|---|---|---|
| `/api/v1/search/federated` | GET | `AllowAny` |
| `/api/v1/search/product` | GET | `AllowAny` |
| `/api/v1/search/blog/post` | GET | `IsBlogEnabled` (404 when the store has blog off) |
| `/api/v1/search/click` | POST | `AllowAny` |
| `/api/v1/search/trending` | GET | `AllowAny` |
| `/api/v1/search/analytics` | GET | `IsStoreStaff` |

`/search/analytics` is **store staff**, not platform superuser: the data is
per-store, and `is_superuser` read on a tenant host comes off a
tenant-schema row (see `is_platform_superuser`).

---

### Federated search

`GET /api/v1/search/federated`

| Parameter | Type | Required | Default |
|---|---|---|---|
| `query` | string | Yes | — |
| `languageCode` | string | No | — |
| `limit` | integer | No | 20 (capped by the `SEARCH_MAX_LIMIT` setting, default 100) |
| `offset` | integer | No | 0 |

Queries the product and blog indexes in one Meilisearch `multi_search` with
federation weights **1.0 for products and 0.7 for blog posts**. Those are
relevance multipliers applied when merging the two result sets — not a fixed
share of the output. If the blog index is unavailable it is dropped from the
federation rather than failing the whole search.

**Response** (`FederatedSearchResponse`):

```json
{
  "queryId": "9a1e6f4c-...",
  "relaxedQuery": null,
  "limit": 20,
  "offset": 0,
  "estimatedTotalHits": 42,
  "results": [ ... ]
}
```

- `queryId` — mint per response; send it back on `/search/click` to attribute
  a click to this query.
- `relaxedQuery` — non-null when the original query returned nothing and the
  view retried with the leading word dropped (see `_relaxed_query`). It tells
  you the results belong to a broader query than the one asked.
- `results` — each item is the serialized product or blog-post translation
  **flattened**, not wrapped in an `object` key. Every item carries
  `contentType`, `rankingScore`, `formatted`, `matchesPosition`, and
  `federation` (`indexUid`, `queriesPosition`, `weightedRankingScore`).

There is **no `processingTimeMs`** on any search response. Meilisearch
reports one, but `meili/querysets.py` does not forward it.

---

### Product search

`GET /api/v1/search/product`

| Parameter | Type | Required | Default |
|---|---|---|---|
| `query` | string | No | `""` |
| `languageCode` | string | No | — |
| `limit` | integer | No | 20 |
| `offset` | integer | No | 0 |
| `categories` | comma-separated ints | No | — |
| `attributeValues` | comma-separated ints | No | — |
| `priceMin`, `priceMax` | number | No | — |
| `likesMin`, `viewsMin` | integer | No | — |
| `sort` | string | No | — |
| `facets` | comma-separated strings | No | — |

There is no `inStock` parameter.

Response is `ProductMeiliSearchResponse`: the common envelope above plus
`facetDistribution` and `facetStats` when `facets` was requested.

---

### Blog post search

`GET /api/v1/search/blog/post`

| Parameter | Type | Required | Default |
|---|---|---|---|
| `query` | string | Yes | — |
| `languageCode` | string | No | — (searches all languages) |
| `limit` | integer | No | 10 |
| `offset` | integer | No | 0 |

Always restricted to published posts. There are no `categoryId` or
`publishedAfter` parameters.

---

### Search click

`POST /api/v1/search/click` → **202 Accepted**, body `{"detail": "Accepted."}`

| Field | Type | Required | Description |
|---|---|---|---|
| `queryId` | UUID | Yes | The `queryId` from the search response |
| `resultId` | string | Yes | Product or BlogPost id |
| `resultType` | string | Yes | `product` or `blog_post` |
| `position` | integer | Yes | 0-indexed position in the result list |

---

### Trending searches

`GET /api/v1/search/trending`

| Parameter | Type | Required | Default |
|---|---|---|---|
| `languageCode` | string | No | — |
| `contentType` | string | No | `product` |
| `limit` | integer | No | 8 (capped at 20) |

Most popular queries from the last 24 hours, cached 5 minutes per
`(languageCode, contentType, limit)`.

```json
{
  "windowHours": 24,
  "contentType": "product",
  "languageCode": null,
  "results": [{ "query": "laptop", "count": 152 }]
}
```

---

### Search analytics

`GET /api/v1/search/analytics` — store staff only.

| Parameter | Type | Required | Default |
|---|---|---|---|
| `startDate` | `YYYY-MM-DD` | No | all history |
| `endDate` | `YYYY-MM-DD` | No | now |
| `contentType` | string | No | — (`product`, `blog_post`, `federated`) |

Returns `dateRange`, `topQueries` (top 20 with `count`, `avgResults`,
`clickThroughRate`), `zeroResultQueries`, `searchVolume`
(`total`, `byContentType`, `byLanguage`), `performance`, and an overall
`clickThroughRate`.

> **`performance.avgProcessingTimeMs` is always `0.0`.** The analytics
> middleware reads `processingTimeMs` off the search response body, and no
> search endpoint emits it, so every `SearchQuery` row stores `NULL` and the
> average falls through to its `or 0.0` default. Treat the number as absent,
> not as "searches take no time". `performance.avgResultsCount` is real.

---

## Behaviour notes

### Greeklish

Transliteration happens at **index time**, not query time — queries reach
Meilisearch verbatim. Each searchable Greek field carries shadow fields
(`*_greeklish`, `*_greeklish_alt`, and `*_greeklish_variants` on short
fields only), so a Latin-typed Greek word matches the indexed variants
directly. See `search/transliteration.py` and the `MeiliMeta` blocks on
`ProductTranslation` / `BlogPostTranslation`. Do not "expand" queries
client-side: Meilisearch's default `last` matching strategy makes the first
word effectively mandatory and only considers the first 10 words.

### Search cutoff

Both indexes set `search_cutoff_ms = 1500` (`product/models/product.py`,
`blog/models/post.py`). Meilisearch returns whatever it found within the
budget; the response carries no timeout flag, so a cut-off search is
indistinguishable from a complete one at the API level.

### Errors

Failures are plain DRF `ValidationError` responses — HTTP 400 with a
field-keyed body, camelized like any other response. There is no error-code
envelope.

```json
{ "limit": ["Must be a valid integer."] }
{ "languageCode": ["Invalid language code."] }
{ "error": ["Search failed. Please try again later."] }
```

### Rate limiting

The global DRF throttles apply; there are no search-specific scopes.
Current rates live in `settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`
(`anon` and `user`, both daily budgets, disabled when `DEBUG`). DRF returns
`429` with a `Retry-After` header — no `X-RateLimit-*` headers are emitted.

## OpenAPI

`schema.yml` is generated by `drf-spectacular` and is the contract the Nuxt
storefront's types are built from. Interactive docs are served at
`/api/v1/schema/swagger-ui` and `/api/v1/schema/redoc`. After changing any
serializer here, regenerate (`manage.py spectacular`) and re-run
`pnpm openapi-ts` + `pnpm sync:schema` in the storefront.
