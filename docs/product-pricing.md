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
| `Product.save()` touching price, markdown or VAT | plans before the save so the derived columns ride the same UPDATE, writes the spells after it; locks the product row | `product/models/product.py`, `product/pricing/service.py: plan_for_save, record_spells` |
| A `Vat` rate edit or deletion | task re-prices the rate's products in batches, then reindexes them | `product/signals.py`, `product/tasks.py: sync_price_records` |
| An unconditional automatic promotion starting, ending or being edited, or a usage limit reached | the offer queue's drain task re-records the products in its scope before reindexing them | `promotion/offer_sync.py: refresh_products`, `promotion/signals.py` |
| A promotion window opening or closing | the 5-minute window sweep | `promotion/offer_sync.py: refresh_window_changes` |
| The nightly reconciliation | recomputes every product, writes only what differs | `product/tasks.py: reevaluate_price_episodes` |

A price field must never be changed with `queryset.update()`: it skips all of
the above (nothing in the code base does today). The nightly sweep exists for
what no path can see: the merchant's `PROMOTIONS_ENABLED` toggle, a promotion
running when this shipped, a product saved by a release that predates the
records. A promotion's start is recorded when the queue or sweep notices it
(seconds to five minutes), not at its `starts_at`.

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
| `effectivePrice` | `ProductSerializer`, search hits | what the customer pays now, promotions included |
| `announcedDiscountPercent` | `ProductSerializer`, search hits, Meilisearch `announced_discount_percent` | integer percent from `priorPrice` to `effectivePrice`, null with no announcement |

All three read stored columns, so a list costs no query per row.
`discountPercent`, `discountValue` and `priceSavePercent` (and the search
`discountPercent`) are the merchant's markdown, not an announcement. They are
**deprecated** in the schema and stay for one release; a client that struck
them through must move to the fields above, after which they are removed with
the expand/contract rule of `docs/migrations.md`. The Meilisearch index needs
`manage.py meilisearch_apply_settings` once for the new displayed fields.

One thing this page does not change and the owner should know:
`Product.final_price` takes VAT on the undiscounted net price and subtracts
the markdown (a percent of the NET price), so a 20% markdown is less than a
20% reduction of the gross price. The announced percent is computed from the
gross prior and effective prices, which is why it differs from
`discountPercent`.
