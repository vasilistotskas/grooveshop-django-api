# Product pricing: the EU Omnibus prior price

Any price reduction a customer is shown (a struck-through "was" price, a
"-N%" badge, an "offer" label) must state the **prior price**: the lowest
price the product was offered at in the 30 days before the reduction began.
This page is the reference for how the API records and decides it. The code
wins over the page; every claim names its file.

The rule is the strictest common denominator of Greece (art. 9ι of Law
2251/1994) and Cyprus (art. 45 of Law 112(I)/2021), so no per-country switch
exists:

- the reference is fixed when the reduction starts, never rolling;
- the announced percentage is computed from that prior price (CJEU C-330/23),
  never from the undiscounted list price;
- a product on sale for less than 30 days announces nothing;
- progressive uninterrupted reductions keep the reference from before the
  first one, but only within 60 days of it; any price increase breaks the
  chain;
- a reduction that stops, even for a day, ends the episode, and the next one
  is new with a new reference;
- there is no exemption for perishables, and the granularity is the Product
  (sibling products of a `ProductVariantGroup` each have their own price).

B2B wholesale prices are outside consumer law and are not tracked: the
records follow the consumer price only.

## What is recorded

| What | Where | Source |
|---|---|---|
| `ProductPriceSpell`: one period during which a product's consumer **gross** unit price was constant (`valid_to` null = current). Append-only; the only mutation is closing the current spell. | table | `product/models/price_spell.py` |
| `Product.effective_price`: the gross price a customer pays now. | stored column | `product/models/product.py` |
| `Product.prior_price`: the frozen reference, null when nothing may be announced. | stored column | same |
| `Product.reduction_started_at`: when the reduction in force began. Set with `prior_price` null means "reduced, but nothing may be announced". | stored column | same |

The tracked price is `Product.final_price` (price plus VAT minus the
markdown) minus what the **unconditional automatic promotions** take off one
unit. Those three columns are derived: `HistoricalRecords` excludes them, and
nothing outside `product/pricing/service.py` writes them.

A promotion counts as a price reduction only when it needs nothing from the
basket: automatic, scoped to products or categories, a percentage or fixed
amount, with no minimum quantity or subtotal, no first-order restriction and
no per-customer limit (`promotion.offers.price_reducing_promotions`).
Conditional promotions, buy-X-get-Y, gifts, free shipping and code promotions
are deals on a basket, not a price a product is offered at, and record
nothing. The amount is computed by the cart's own rules over a one-line cart
(`PromotionEngine.single_unit_discount`: scope, exclusions, ceiling, stacking,
clamp), so a listing and the cart agree to the cent. A fixed amount is taken
off one unit.

## How a reduction is decided

`product/pricing/evaluation.py` holds the rule as one pure function,
`evaluate_reduction(spells, first_offered_at, is_reduction, episode, now)`.
It returns the episode in force or `None`:

- `is_reduction` is a markdown (`discount_percent > 0`) or an unconditional
  promotion lowering the price;
- a new episode starts at `now`: the reference is the minimum price of the
  spells overlapping `[start - 30 days, start)`; none if the product's first
  spell is younger than 30 days; no episode if the price is not below that
  minimum;
- an episode in force holds while every price change since its start was a
  drop, each within 60 days of the start, and the price stays below the
  reference. An increase, a drop after 60 days, or the price reaching the
  reference ends it and a new one is anchored;
- `announced_discount_percent(prior, current)` is the floor of the drop from
  the prior price.

`constants.py` next to it holds the 30 and 60 days: statutory numbers, not
deployment settings.

## Every path that moves a price

| Path | How it records | Source |
|---|---|---|
| `Product.save()` touching price, markdown or VAT | plans before the save so the derived columns ride the same UPDATE, writes the spells after it; takes no lock of its own (constraint-guarded) | `product/models/product.py`, `product/pricing/service.py: plan_for_save, record_spells` |
| A `Vat` rate edit or deletion | task re-prices the rate's products in batches, then reindexes them | `product/signals.py`, `product/tasks.py: sync_price_records` |
| An unconditional automatic promotion starting, ending or being edited, or a usage limit reached | the offer queue's drain task re-records the products in its scope before reindexing them | `promotion/offer_sync.py: refresh_products`, `promotion/signals.py` |
| A promotion window opening or closing | the 5-minute window sweep | `promotion/offer_sync.py: refresh_window_changes` |
| The nightly reconciliation | recomputes every product, writes only what differs | `product/tasks.py: reevaluate_price_episodes` |

A price field must never be changed with `queryset.update()`: it skips all of
the above (nothing in the code base does today). The nightly sweep exists for
what no path can see: a promotion running when this shipped, a product saved
by a release that predates the records. Switching promotions off or on (the
store's `PROMOTIONS_ENABLED`, the plan's `promotions_enabled`) runs the same
sweep for that store at once (`promotion/signals.py`). A promotion's start is recorded when the queue or sweep notices it
(seconds to five minutes), not at its `starts_at`.

Those price columns move through `bulk_update`, which sends no `post_save`,
so `sync_product_prices` queues the `products` cache-surface purge itself
(`core.cache.invalidation.mark_surface_stale`) when it changed anything: a VAT
edit, a promotion window and the nightly sweep reach the storefront without
waiting for the cache TTL. A promotion window passing
(`promotion.offer_sync.refresh_window_changes`) and the admin's coupon-code
generator do the same for the `promotions` surface. Reviews, blog comments and
promotion codes are on their surfaces' `invalidated_by`, and the bulk
moderation actions save row by row, so approving, rejecting or hiding one
shows at once.

Spells are legal records. `clean_old_history` only touches simple-history
tables, and nothing deletes a spell.

## The backfill

`product` migration `0050_backfill_price_spells` replays `HistoricalProduct`
(at most 365 days) through the same evaluation function, per schema, so a
product whose markdown started last week has its reference immediately. It is
idempotent (a product with spells is skipped) and loads no live model code.
What it cannot know:

- prices older than the oldest surviving history row: a product with no
  history gets one spell from `created_at`, and any long-running markdown on
  it announces nothing;
- the VAT rate of each historical row (history keeps the VAT id): the current
  rate is applied;
- automatic promotions (`Promotion` has no history): one already running is
  picked up by the next nightly sweep, with its start recorded then.
- anything the previous release saves while it still serves after the
  migration: it does not write the derived columns, so the nightly sweep
  reconciles them.

## API

| Field | Where | Meaning |
|---|---|---|
| `priorPrice` | `ProductSerializer`, search hits, Meilisearch `prior_price` | the reference, null when nothing may be announced |
| `reductionStartedAt` | `ProductSerializer`, `ProductVariantSerializer`, `AgentFavouriteSerializer` (one field, `AnnouncedReductionStartField`, in `EffectivePriceFieldsSerializer`) | when the reduction in force began; null exactly when `priorPrice` is, because `Product.reduction_started_at` is also set for a reduction that announces nothing. Not in search hits: they are index documents, and the field would need a Meilisearch attribute and a reindex |
| `effectivePrice` | `ProductSerializer`, search hits | what the customer pays now, promotions included |
| `announcedDiscountPercent` | `ProductSerializer`, search hits, Meilisearch `announced_discount_percent` | integer percent from `priorPrice` to `effectivePrice`, null with no announcement |
| `effectivePrice`, `priorPrice`, `announcedDiscountPercent` | `AgentFavouriteSerializer` (`/api/v1/agent/me/favourites`) | the same three fields, one definition (`EffectivePriceFieldsSerializer`) shared with `ProductSerializer`; the row's `finalPrice` is **deprecated** there, to go once the agent gateway reads `effectivePrice` |
| `effectivePrice`, `priorPrice`, `announcedDiscountPercent` | `ProductVariantSerializer` (`/api/v1/product/{id}/variants`) | each sibling's own three fields, the same `EffectivePriceFieldsSerializer`, so a variant swatch or an agent's variant list shows the price and announcement its product card shows |
| `?minAnnouncedDiscountPercent=` / `?ordering=-announcedDiscountPercent` | `ProductFilter`, `ProductViewSet.ordering_fields` | list filter and sort on the same percent (`with_announced_discount_percent()`); the discounted rail uses both |
| `?minEffectivePrice=` / `?maxEffectivePrice=` / `?ordering=effectivePrice` | `ProductFilter`, `ProductViewSet.ordering_fields` | list price filter and sort on `effective_price`, a stored column |
| search `price_min` / `price_max` / `sort=effectivePrice` / `facets=effective_price` | `search/views.py`, Meilisearch `effective_price` (filterable, sortable, displayed) | price filter, sort and the price-range slider stats on the same price |

Price filter and price sort follow what the customer pays, not the list
price: a product under a 20% automatic category promotion is found by a price
filter at its promoted price and sorts there, while a conditional promotion
(minimum quantity or subtotal, first order, per-customer limit) changes neither,
because the cart only honours it for a basket that meets the condition. B2B
group prices are not in the index or the filter either (see above).

The price before promotions (`finalPrice`) is still a field of the product and of
every search hit: the cart and orders read it, and the storefront's product card
falls back to it for a hit whose `effectivePrice` is not recorded yet. Its
filter, facet and sort surfaces are gone: `minFinalPrice` / `maxFinalPrice`, the
`final_price` search facet (and `facetStats.finalPrice`), the search
`sort=finalPrice` / `-finalPrice` and the Meilisearch `final_price` filterable
and sortable attributes were removed once the storefront and the agent gateway
filtered, ranged and sorted on the effective price (expand/contract for API
contracts, `docs/migrations.md`). An unknown `facets` or `sort` value is
ignored by the search view, so an old client asking for `final_price` gets no
stats, or the default order, rather than an error. The Meilisearch
`final_price` document field and displayed attribute stay for as long as a hit's
`finalPrice` is read. The search `price_min` / `price_max` parameters keep
their names and compare with `effective_price`.

**Known recording lag.** The price is recorded when the offer queue or the
5-minute window sweep notices a promotion, then the product is reindexed, so a
promotion's effect on filter, sort and index lands within about five minutes of
its `starts_at` / `ends_at` (seconds for an edit, through the queue). In that
window the listing, the filter and the cart can disagree. A product whose
`effective_price` is not recorded yet (null) is absent from the price filter
and sorts last until the nightly reconciliation fills it.

Every price field above reads a stored column, so a list costs no query per row.
`discountPercent`, `discountValue` and `priceSavePercent` are the merchant's
markdown, not an announcement, and are gone from the product and search-hit
payloads (and `discount_percent` from the Meilisearch displayed attributes):
a client that struck a price through must use the fields above. The
merchant's input (`Product.discount_percent`, the admin form, the product
write API, the `minDiscountPercent` / `maxDiscountPercent` filters, the
`discountPercent` ordering, and the indexed `discount_percent` that the
`stock:desc, discount_percent:desc` ranking rule and the on-offer flag use) is
unchanged.

The deploy's PreSync hook applies the new displayed, filterable and sortable fields
(`prior_price`, `announced_discount_percent`, `effective_price`) with
`meilisearch_apply_settings --all-tenants`. Documents indexed before such a
field existed only gain it when they are rebuilt, so a release that adds one
is followed by
`manage.py meilisearch_sync_index product.ProductTranslation --all-tenants`;
without `--all-tenants` the command runs in the public schema, which holds no
products. Taking an attribute out of the filterable list (as `final_price` was)
needs only the settings step: the documents keep the field, so no reindex.

## The demo store's price history

The demo catalogue is seeded already marked down, so left alone its products
have no full-price spell older than 30 days and the rule above announces
nothing. The `price-history` step of `manage.py seed_demo_store`
(`devtools/demo_price_history.py`) therefore records, for every marked-down
demo product, a full-price spell that began before the look-back window and
ends where the reduction starts, a fixed point inside the 60-day progressive
window. Only that full-price spell is written by the seed; the reduction's
spell and `effective_price` / `prior_price` / `reduction_started_at` come from
`sync_product_prices`, the same path the app uses. It is demo-only because it
deletes and back-dates spells, which the platform never does: it touches only
the seed's own `demo-` products. Re-running changes nothing while a product's
history is already what the seed writes.

The nightly demo reset (`devtools/demo_account.py: reset_demo_account`) runs
the same step, so a history that a price change broke, or one that was never
seeded, is repaired by the next night, and the store's caches are purged when
anything was rebuilt. Until 2026-10-16 the step ran only from
`seed_demo_store`, which production's demo never got: all its markdowns were
unannounced. To repair one at once, run
`manage.py seed_demo_store --schema demo --only price-history` followed by
`manage.py seed_demo_store --schema demo --only cache-purge`.

## The markdown formula

The markdown reduces the NET price and VAT is charged on what is left:
`final_price = price * (1 - p) * (1 + vat)`, so a 10% markdown takes 10% off
what the shopper pays (30.00 net at 24% VAT: 37.20 list, 33.48 with 10% off).
`Product.discount_value` is the markdown on the net price, `Product.vat_value`
is the VAT of the price actually charged, and
`final_price == price - discount_value + vat_value`. The SQL twins in
`product/managers/product.py` follow the same definition. Before migration
`0051` the VAT was taken on the undiscounted net, which made a 10% markdown
worth about 8%.

`0051_reprice_discounted_products` records that change as an ordinary price
drop: for every marked-down product whose current spell still holds the old
formula's price, the spell is closed, a new one opens at the new price and the
episode is re-evaluated by `evaluate_reduction` (a drop inside an episode keeps
its reference within 60 days of the start; later it is a new reduction measured
against the last 30 days). Products carrying a promotion are left to the nightly
sweep, which re-prices them the same way. Spells written before the change keep
the prices customers were actually charged.
