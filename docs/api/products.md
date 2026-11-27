# Product list and feed API

The two paginated product listings and who reads each.

**Source of truth:** `src/product/views/product.py` (`ProductViewSet`:
`list`, `feed`, `get_queryset`, `action_ordering`), `src/product/urls.py`,
`src/product/serializers/product.py` (`ProductCardSerializer`,
`ProductSerializer`, `ProductFeedSerializer`), `src/product/managers/product.py`
(`for_list`, `for_full_list`, `for_feed`), `src/product/filters/product.py`, and the generated
`schema.yml`. Where this page and the code disagree, the code wins.

## Endpoints

| Endpoint | Operation | Serializer | Queryset | Read by |
|---|---|---|---|---|
| `GET /api/v1/product` | `listProduct` | `ProductCardSerializer` | `for_list()` | the storefront's product rails, sitemap and RSS |
| `GET /api/v1/product/feed` | `listProductFeed` | `ProductFeedSerializer` | `for_feed()` + `with_offer_kind()` | the agent gateway's catalogue feeds (`/feeds/*`) |

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
`potentialPoints`, `mainImagePath`, `mainImageWidth`, `mainImageHeight`,
`reviewAverage`, `reviewCount`, `createdAt`, `updatedAt` and `uuid` (the RSS
item GUID).

`for_list()` loads the translations, the category, the counts, the announced
discount and the main image, and no gallery, attributes or variant group. The
like, review-count and review-average annotations are there although the card
shows only the review ones: the list sorts on `likesCount` too.

A product's attributes, gallery (`imagePaths`), variant group
(`variantGroup`, `variantGroupTitle`), `offerKind`, `reductionStartedAt`,
the net price and VAT fields, weight, counters and SEO copy are not in the
list. The product page (`/product/{id}`) and the feed carry them.
`ProductSerializer` is the shape wherever a product is nested (cart and order
lines, reviews, images, recommendations). `for_full_list()` is the queryset for
a list rendered through it (`src/recommendation/hydrate.py`); `for_feed()` adds
the highlights, which `ProductFeedSerializer` publishes on top of that shape.

## Ordering

`ordering` accepts the keys in `ProductViewSet.ordering_fields`; the
default is `-availability_priority, id` (in stock first). Every ordering
ends in the primary key, so products that tie keep one order across pages.

An extra `@action` is unsorted unless it declares an ordering contract
(`src/core/filters/camel_case_ordering.py`, `ordering_for`), so `feed` opts
into the list's own through `action_ordering["feed"]`.

## Changing either shape

Removing a field from a response follows the storefront-first rule in
`AGENTS.md` ("API fields follow the same rule"): the storefront release that
no longer requires the field reaches production first, then Django drops it.
`tests/integration/product/product/test_view_product_feed.py` pins that a
list row is the feed row cut down to the card's fields.
