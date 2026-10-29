# Product list and feed API

The two paginated product listings and who reads each.

**Source of truth:** `product/views/product.py` (`ProductViewSet`:
`list`, `feed`, `get_queryset`, `action_ordering`), `product/urls.py`,
`product/serializers/product.py` (`ProductCardSerializer`,
`ProductSerializer`), `product/managers/product.py` (`for_list`,
`for_full_list`), `product/filters/product.py`, and the generated
`schema.yml`. Where this page and the code disagree, the code wins.

## Endpoints

| Endpoint | Operation | Serializer | Queryset | Read by |
|---|---|---|---|---|
| `GET /api/v1/product` | `listProduct` | `ProductCardSerializer` | `for_list()` | the storefront's product rails, sitemap and RSS |
| `GET /api/v1/product/feed` | `listProductFeed` | `ProductSerializer` | `for_full_list()` + `with_offer_kind()` | the agent gateway's catalogue feeds (`/feeds/*`) |

Both are `AllowAny` behind `IsCatalogueEnabled` (404 when the store has
the catalogue off), list active products only, and take the same query
parameters: every `ProductFilter` filter, `search`, `languageCode`, the
pagination parameters (default page size 12, maximum 100) and `ordering`.
For one query they return the same products in the same order.

## The list row is a product card

`ProductCardSerializer` carries what the storefront's product card
(`app/components/Product/Card.vue` and the add-to-cart, favourite and
notify-me controls it renders), the sitemap and the RSS feed read:
`id`, `translations` (`name` and `description` per language), `slug`,
`category`, `brandName`, `stock`, `lowStockThreshold`, `active`,
`finalPrice`, `effectivePrice`, `priorPrice`, `announcedDiscountPercent`,
`mainImagePath`, `reviewAverage`, `reviewCount`, `createdAt`, `updatedAt`
and `uuid` (the RSS item GUID).

`for_list()` prefetches the translations and the main image only. The
like, review-count and review-average annotations stay although the card
shows only the review ones: the list sorts on `likesCount` too.

A product's attributes, gallery (`imagePaths`), variant group
(`variantGroup`, `variantGroupTitle`), `offerKind`, `reductionStartedAt`,
the net price and VAT fields, weight, counters and SEO copy are not in the
list. The product page (`/product/{id}`) and the feed carry them.
`ProductSerializer` is unchanged where it is nested (cart and order lines,
reviews, images, recommendations), and `for_full_list()` is the queryset
for a list rendered through it (the feed and `recommendation/hydrate.py`).

## Ordering

`ordering` accepts the keys in `ProductViewSet.ordering_fields`; the
default is `-availability_priority, id` (in stock first). Every ordering
ends in the primary key, so products that tie keep one order across pages.

An extra `@action` is unsorted unless it declares an ordering contract
(`core/filters/camel_case_ordering.py`, `ordering_for`), so `feed` opts
into the list's own through `action_ordering["feed"]`.

## Changing either shape

Removing a field from a response follows the storefront-first rule in
`CLAUDE.md` ("API fields follow the same rule"). The list lost its
feed-only fields in this order: the feed was added, the gateway moved to
it, the storefront release that no longer requires the fields reached
production, and only then did Django drop them.
`tests/integration/product/product/test_view_product_feed.py` pins that a
list row is the feed row cut down to the card's fields.
