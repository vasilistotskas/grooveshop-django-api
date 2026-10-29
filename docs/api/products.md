# Product list and feed API

The two paginated product listings and who reads each.

**Source of truth:** `product/views/product.py` (`ProductViewSet`:
`list`, `feed`, `get_queryset`, `action_ordering`), `product/urls.py`,
`product/serializers/product.py`, `product/managers/product.py`
(`for_list`), `product/filters/product.py`, and the generated
`schema.yml`. Where this page and the code disagree, the code wins.

## Endpoints

| Endpoint | Operation | Serializer | Read by |
|---|---|---|---|
| `GET /api/v1/product` | `listProduct` | `ProductSerializer` | the storefront's product rails, sitemap and RSS |
| `GET /api/v1/product/feed` | `listProductFeed` | `ProductSerializer` | the agent gateway's catalogue feeds (`/feeds/*`) |

Both are `AllowAny` behind `IsCatalogueEnabled` (404 when the store has
the catalogue off), list active products only (`Product.objects.for_list()`),
and take the same query parameters: every `ProductFilter` filter,
`search`, `languageCode`, the pagination parameters (default page size 12,
maximum 100) and `ordering`.

## Ordering

`ordering` accepts the keys in `ProductViewSet.ordering_fields`; the
default is `-availability_priority, id` (in stock first). Every ordering
ends in the primary key, so products that tie keep one order across pages.

An extra `@action` is unsorted unless it declares an ordering contract
(`core/filters/camel_case_ordering.py`, `ordering_for`), so `feed` opts
into the list's own through `action_ordering["feed"]`. Without it the feed
would ignore `ordering` and page in an unspecified order.

## Why two listings

The list serves product cards, which read a few fields per product. A
catalogue feed reads the whole product: every attribute (`attributes`),
every image (`imagePaths`) and the variant group's name
(`variantGroupTitle`), for each product of the store, page by page.
`feed` exists so the list can drop what only a feed reads without taking
it from the gateway. Until then the two answer one query with the same
rows in the same shape (`tests/integration/product/product/test_view_product_feed.py`).

Removing a field from the list follows the storefront-first rule in
`CLAUDE.md` ("API fields follow the same rule"): the gateway reads the
feed first, the storefront release that no longer requires the field
reaches production, and only then does Django drop it.
