# Search API Documentation

## Overview

The Search API provides federated search capabilities across multiple content types (products, blog posts) with advanced features including:

- Multi-index federated search
- Instant search with debouncing
- Multi-language support (English, Greek, German)
- Greeklish query expansion
- Search analytics and tracking
- Content type filtering
- Pagination and result limiting

## Base URL

```
https://api.webside.gr/api/v1
```

## Authentication

Most search endpoints are publicly accessible (`AllowAny`). `GET /api/v1/search/analytics` is
restricted to platform superusers (`IsPlatformSuperuser`).

## Endpoints

### 1. Federated Search

Perform a federated search across products and blog posts.

**Endpoint:** `GET /api/v1/search/federated`

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | Yes | - | Search query string |
| `language_code` | string | Yes | - | Language code (`en`, `el`, `de`) |
| `limit` | integer | No | 20 | Maximum number of results (1-100) |
| `offset` | integer | No | 0 | Pagination offset |
| `content_type` | string | No | - | Filter by content type (`product`, `blog_post`) |

**Example Request:**

```bash
curl -X GET "https://api.webside.gr/api/v1/search/federated?query=laptop&language_code=en&limit=20"
```

**Example Response:**

```json
{
  "results": [
    {
      "id": "123",
      "content_type": "product",
      "_rankingScore": 0.95,
      "_federation": {
        "indexUid": "product_translations_en",
        "queriesPosition": 0,
        "weightedRankingScore": 0.95
      },
      "_formatted": {
        "name": "<mark>Laptop</mark> Pro 15",
        "description": "High-performance <mark>laptop</mark> for professionals"
      },
      "object": {
        "id": 123,
        "name": "Laptop Pro 15",
        "slug": "laptop-pro-15",
        "price": "1299.99",
        "currency": "EUR",
        "active": true,
        "language_code": "en"
      }
    },
    {
      "id": "456",
      "content_type": "blog_post",
      "_rankingScore": 0.87,
      "_federation": {
        "indexUid": "blog_post_translations_en",
        "queriesPosition": 1,
        "weightedRankingScore": 0.87
      },
      "_formatted": {
        "title": "Best <mark>Laptops</mark> of 2024",
        "excerpt": "Comprehensive guide to choosing the perfect <mark>laptop</mark>"
      },
      "object": {
        "id": 456,
        "title": "Best Laptops of 2024",
        "slug": "best-laptops-2024",
        "is_published": true,
        "language_code": "en"
      }
    }
  ],
  "limit": 20,
  "offset": 0,
  "estimated_total_hits": 42,
  "processing_time_ms": 15
}
```

**Response Fields:**

- `results`: Array of search results
  - `id`: Unique identifier for the result
  - `content_type`: Type of content (`product` or `blog_post`)
  - `_rankingScore`: Relevance score (0-1)
  - `_federation`: Federation metadata
    - `indexUid`: Source index identifier
    - `queriesPosition`: Position in federated query
    - `weightedRankingScore`: Weighted relevance score
  - `_formatted`: Formatted fields with search highlights
  - `object`: Full object data
- `limit`: Number of results per page
- `offset`: Current pagination offset
- `estimated_total_hits`: Estimated total number of matching results
- `processing_time_ms`: Search processing time in milliseconds

**Status Codes:**

- `200 OK`: Successful search
- `400 Bad Request`: Invalid parameters
- `500 Internal Server Error`: Server error

---

### 2. Product Search

Search specifically for products.

**Endpoint:** `GET /api/v1/search/product`

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | No | `""` | Search query string |
| `language_code` | string | No | - | Language code (`en`, `el`, `de`) |
| `limit` | integer | No | 20 | Maximum number of results (1-100) |
| `offset` | integer | No | 0 | Pagination offset |
| `categories` | comma-separated integers | No | - | Filter by category IDs |
| `attribute_values` | comma-separated integers | No | - | Filter by product attribute value IDs |
| `price_min` | decimal | No | - | Minimum price filter |
| `price_max` | decimal | No | - | Maximum price filter |
| `likes_min` | integer | No | - | Minimum likes-count filter |
| `views_min` | integer | No | - | Minimum views-count filter |
| `sort` | string | No | - | Sort expression |
| `facets` | comma-separated strings | No | - | Facets to compute |

There is no `in_stock` parameter.

**Example Request:**

```bash
curl -X GET "https://api.webside.gr/api/v1/search/product?query=laptop&language_code=en&price_min=500&price_max=2000"
```

**Example Response:**

```json
{
  "results": [
    {
      "id": 123,
      "name": "Laptop Pro 15",
      "slug": "laptop-pro-15",
      "description": "High-performance laptop for professionals",
      "price": "1299.99",
      "currency": "EUR",
      "image_url": "https://assets.webside.gr/products/laptop-pro-15.jpg",
      "category": "Computers",
      "active": true,
      "_rankingScore": 0.95
    }
  ],
  "limit": 20,
  "offset": 0,
  "estimated_total_hits": 15,
  "processing_time_ms": 12
}
```

---

### 3. Blog Post Search

Search specifically for blog posts.

**Endpoint:** `GET /api/v1/search/blog/post`

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | Yes | - | Search query string |
| `language_code` | string | No | - | Language code (`en`, `el`, `de`). If omitted, searches all languages |
| `limit` | integer | No | 10 | Maximum number of results |
| `offset` | integer | No | 0 | Pagination offset |

There are no `category_id` or `published_after` parameters — results are always
filtered to published posts (`is_published=True`).

**Example Request:**

```bash
curl -X GET "https://api.webside.gr/api/v1/search/blog/post?query=laptop&language_code=en"
```

**Example Response:**

```json
{
  "results": [
    {
      "id": 456,
      "title": "Best Laptops of 2024",
      "slug": "best-laptops-2024",
      "excerpt": "Comprehensive guide to choosing the perfect laptop",
      "content": "...",
      "image_url": "https://assets.webside.gr/blog/best-laptops-2024.jpg",
      "published_at": "2024-01-15T10:00:00Z",
      "is_published": true,
      "_rankingScore": 0.87
    }
  ],
  "limit": 20,
  "offset": 0,
  "estimated_total_hits": 8,
  "processing_time_ms": 10
}
```

---

### 4. Search Analytics

Retrieve search analytics and metrics. **Requires a platform superuser** (`IsPlatformSuperuser`).

**Endpoint:** `GET /api/v1/search/analytics`

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `start_date` | date | No | all historical data | Start date for analytics (`YYYY-MM-DD`) |
| `end_date` | date | No | up to today | End date for analytics (`YYYY-MM-DD`) |
| `content_type` | string | No | - | Filter by content type (`product`, `blog_post`, `federated`) |

**Example Request:**

```bash
curl -X GET "https://api.webside.gr/api/v1/search/analytics?start_date=2024-01-01&end_date=2024-12-31" \
  -H "Authorization: Bearer <platform-superuser-token>"
```

**Example Response:**

```json
{
  "date_range": {
    "start": "2024-01-01",
    "end": "2024-12-31"
  },
  "top_queries": [
    {
      "query": "laptop",
      "count": 1523,
      "avg_results": 42,
      "click_through_rate": 0.68
    },
    {
      "query": "smartphone",
      "count": 987,
      "avg_results": 35,
      "click_through_rate": 0.72
    }
  ],
  "zero_result_queries": [
    {
      "query": "nonexistent product",
      "count": 15,
      "language_code": "en"
    }
  ],
  "search_volume": {
    "total": 45678,
    "by_content_type": {
      "product": 32145,
      "blog_post": 8234,
      "federated": 5299
    },
    "by_language": {
      "en": 28456,
      "el": 12345,
      "de": 4877
    }
  },
  "performance": {
    "avg_processing_time_ms": 18,
    "avg_results_count": 38
  },
  "click_through_rate": 0.65
}
```

**Response Fields:**

- `date_range`: Date range for analytics
- `top_queries`: Top 20 queries by frequency
  - `query`: Search query text
  - `count`: Number of times searched
  - `avg_results`: Average number of results
  - `click_through_rate`: Percentage of searches resulting in clicks
- `zero_result_queries`: Queries that returned no results
- `search_volume`: Search volume statistics
  - `total`: Total number of searches
  - `by_content_type`: Breakdown by content type
  - `by_language`: Breakdown by language
- `performance`: Performance metrics
  - `avg_processing_time_ms`: Average search processing time
  - `avg_results_count`: Average number of results per search
- `click_through_rate`: Overall click-through rate

---

### 5. Search Click

Attribute a click on a search result to the query that produced it. Feeds the
click-through ranking signal and search analytics.

**Endpoint:** `POST /api/v1/search/click`

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query_id` | UUID | Yes | The `query_id` returned by the search response |
| `result_id` | string | Yes | ID of the clicked result (Product or BlogPost ID) |
| `result_type` | string | Yes | `product` or `blog_post` |
| `position` | integer | Yes | 0-indexed position of the result in the result list |

**Example Request:**

```bash
curl -X POST "https://api.webside.gr/api/v1/search/click" \
  -H "Content-Type: application/json" \
  -d '{"queryId": "9a1e...", "resultId": "123", "resultType": "product", "position": 0}'
```

**Example Response (202 Accepted):**

```json
{
  "detail": "Accepted."
}
```

---

### 6. Search Trending

Return the most popular search queries from the last 24 hours, suitable for
surfacing in the search modal's empty state. Cached for 5 minutes per
`(language_code, content_type, limit)`.

**Endpoint:** `GET /api/v1/search/trending`

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `language_code` | string | No | - | Filter queries by language (e.g. `el`) |
| `content_type` | string | No | `product` | `product`, `blog_post`, or `federated` |
| `limit` | integer | No | 8 | Max results (capped at 20) |

**Example Request:**

```bash
curl -X GET "https://api.webside.gr/api/v1/search/trending?content_type=product&limit=8"
```

**Example Response:**

```json
{
  "window_hours": 24,
  "content_type": "product",
  "language_code": null,
  "results": [
    { "query": "laptop", "count": 152 },
    { "query": "smartphone", "count": 98 }
  ]
}
```

---

## Features

### Federated Search

Federated search queries multiple indexes simultaneously and merges results with weighted prioritization:

- **Products**: Weight 1.0 (70% of results)
- **Blog Posts**: Weight 0.7 (30% of results)

Results include `_federation` metadata showing the source index and weighted ranking score.

### Greeklish Support

When searching with `language_code=el`, the system automatically expands Greeklish queries to include Greek equivalents:

- `kompiouter` → `κομπιούτερ`
- `laptop` → `λάπτοπ`

This provides better search results for Greek users typing in Latin characters.

### Search Highlights

Search results include `_formatted` fields with HTML `<mark>` tags highlighting matched terms:

```json
{
  "_formatted": {
    "name": "High-performance <mark>laptop</mark> for professionals"
  }
}
```

### Content Filtering

Federated search automatically filters content:

- **Products**: Only active, non-deleted products (`active=true AND is_deleted=false`)
- **Blog Posts**: Only published posts (`is_published=true`)

### Search Cutoff

All search endpoints have a 1500ms timeout (searchCutoffMs). If a query exceeds this limit:

- Partial results are returned
- Response includes timeout indicator
- Query is logged for analysis

### Pagination

Use `limit` and `offset` parameters for pagination:

```bash
# Page 1 (results 0-19)
GET /api/v1/search/federated?query=laptop&limit=20&offset=0

# Page 2 (results 20-39)
GET /api/v1/search/federated?query=laptop&limit=20&offset=20

# Page 3 (results 40-59)
GET /api/v1/search/federated?query=laptop&limit=20&offset=40
```

---

## Error Handling

### Error Response Format

All errors follow a consistent format:

```json
{
  "error": {
    "code": "SEARCH_TIMEOUT",
    "message": "Search query exceeded time limit",
    "details": {
      "query": "laptop",
      "cutoff_ms": 1500,
      "partial_results": true
    }
  }
}
```

### Common Error Codes

| Code | Status | Description |
|------|--------|-------------|
| `INVALID_QUERY` | 400 | Query parameter is missing or invalid |
| `INVALID_LANGUAGE` | 400 | Language code is not supported |
| `INVALID_LIMIT` | 400 | Limit parameter is out of range (1-100) |
| `SEARCH_TIMEOUT` | 200 | Query exceeded time limit (partial results returned) |
| `SEARCH_ERROR` | 500 | Internal search engine error |
| `SERVICE_UNAVAILABLE` | 503 | Search service is temporarily unavailable |

---

## Rate Limiting

Search endpoints are rate-limited to prevent abuse:

- **Anonymous users**: 100 requests per minute
- **Authenticated users**: 500 requests per minute

Rate limit headers are included in responses:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1640995200
```

---

## Best Practices

### Debouncing

Implement debouncing on the client side to reduce API calls during rapid typing:

```javascript
// Recommended: 150ms debounce
const debouncedSearch = debounce(searchFunction, 150)
```

### Request Cancellation

Cancel pending requests when starting a new search:

```javascript
const controller = new AbortController()

fetch('/api/v1/search/federated?query=laptop', {
  signal: controller.signal
})

// Cancel when new search starts
controller.abort()
```

### Caching

Cache search results on the client side for frequently searched queries:

```javascript
const cache = new Map()

if (cache.has(query)) {
  return cache.get(query)
}

const results = await searchAPI(query)
cache.set(query, results)
```

### Error Handling

Always handle errors gracefully:

```javascript
try {
  const results = await searchAPI(query)
  displayResults(results)
} catch (error) {
  if (error.code === 'SEARCH_TIMEOUT') {
    displayPartialResults(error.details.partial_results)
  } else {
    displayErrorMessage('Search failed. Please try again.')
  }
}
```

---

## Examples

### Basic Federated Search

```bash
curl -X GET "https://api.webside.gr/api/v1/search/federated?query=laptop&language_code=en"
```

### Filtered Product Search

```bash
curl -X GET "https://api.webside.gr/api/v1/search/product?query=laptop&language_code=en&price_min=500&price_max=2000"
```

### Paginated Blog Search

```bash
curl -X GET "https://api.webside.gr/api/v1/search/blog/post?query=technology&language_code=en&limit=10&offset=20"
```

### Greek Search with Greeklish

```bash
curl -X GET "https://api.webside.gr/api/v1/search/federated?query=kompiouter&language_code=el"
```

### Analytics for Specific Period

```bash
curl -X GET "https://api.webside.gr/api/v1/search/analytics?start_date=2024-01-01&end_date=2024-01-31&content_type=product" \
  -H "Authorization: Bearer <platform-superuser-token>"
```

---

## OpenAPI Schema

The complete OpenAPI schema is generated by `drf-spectacular` and served at:

```
https://api.webside.gr/api/v1/schema
```

Interactive docs: `https://api.webside.gr/api/v1/schema/swagger-ui` and
`https://api.webside.gr/api/v1/schema/redoc`.

TypeScript types and Zod schemas are auto-generated from the OpenAPI schema for type-safe frontend integration (`pnpm openapi-ts` in the Nuxt storefront).

---

## Support

For questions or issues with the Search API:

- **GitHub Issues**: https://github.com/vasilistotskas/grooveshop-django-api/issues
