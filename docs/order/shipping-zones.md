# Order system: shipping zones

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 5.5 Where a store delivers: `ShippingZone`

`Country` and `Region` are public reference data, the same rows for every
store (`SHARED_APPS`; deleting one a store uses is refused,
[§3.3](creation-address.md)). Where a store delivers, and at what price, is
the store's own configuration on top of them, in its schema:

- **`shipping.ShippingZone`** (`src/shipping/models/zone.py`): a staff-facing
  `name` (unique), one `country`, and `regions` of that country. A zone with
  no regions covers the whole country: the country's **base zone**. A zone
  with regions covers only those, and **overrides the base zone for them**.
- **`shipping.ShippingZoneRegion`**: the region set, a through model whose
  `region` is unique, so in one store a region is in one zone at most and
  every destination resolves to one zone.
- **`shipping.ShippingRate.zone`**: a rate belongs to a zone, one per
  `(provider, zone, kind)` ([§5.0](shipping.md)).

A zone with no rate for a kind does not deliver that kind there. "The
islands cost more" is a region zone with its own rates, delivery days and
free threshold; "we do not deliver to Crete" is a region zone with no
home-delivery rate. There is no surcharge field: a zone's own rates carry
everything that differs (price, free threshold, weight cap, delivery days),
so a price has one source and every "free above X" rule judges it as it
does any other rate.

### Which zone prices a destination (`src/shipping/zones.py`)

- **Home delivery to `(country, region)`**: the region zone holding the
  region, else the country's base zone (`zone_for`). With no region (a
  country without regions, or a checkout before the shopper picks one) the
  base zone.
- **Pickup point in a country**: the base zone's pickup rates. Neither
  carrier's catalogue gives a point a `Region` (ACS's station `region` is its
  own free text, BOX NOW's lockers have none), so a point is judged by its
  country. A pickup rate on a region zone would never apply, so the admin
  refuses it (`rate_kind_errors`).
- `destination_rates(country, region)` is that resolution as a queryset, and
  every gate reads it: `ShippingService.active_rate` / `assert_available` /
  `quote` / `resolve_live_price`, `offered_rates` (the options and
  `/shipping/policy`), `free_threshold_rates`,
  `resolve_home_delivery_provider` and `Order.estimated_delivery`
  (`OrderService._seed_estimated_delivery`). The quote, both order-creation
  paths and the payment intent pass the order's region for a home delivery,
  so the price charged and the carrier assigned come from the same zone.
- The carrier's `live_quote()` (ACS) is asked with the rate the zone
  resolved and the region, as before.
- **Without a region the base zone answers**: `/shipping/options` without
  `regionId`, and every surface that knows no address (`/shipping/policy`,
  `/shipping/free-shipping-info`, `/shipping/product/<id>`, the cart's
  `freeShippingProgress`), which judge the store's first shipping country.
  A country served only by region zones advertises nothing there until a
  region is known.

### Served destinations

- **A country is shippable** (`/country?shippable=true`,
  `ShippingService.shippable_country_codes`) when any of its zones has an
  active rate with an active provider, pickup included.
- **A region is served** when a home delivery reaches it: an active
  home-delivery rate with an active provider in its resolved zone
  (`HomeCoverage`, read in one pass). The region picker belongs to a home
  address; a pickup order keeps no region ([§3.3](creation-address.md)).
  `?shippable=true` on `GET /region` and on
  `GET /region/<alpha2>/get_regions_by_country_alpha_2` keeps the served
  regions, `false` the others; on the platform host `true` lists none.

### Refusals (coded, [error contract](../error-contract.md))

| Code | Where | Meaning |
|---|---|---|
| `shipping_region_unavailable` | `POST /cart/quote` and the payment intent (`ShippingRefusal.REGION_UNAVAILABLE`) | the chosen delivery reaches the country, but its rate is not in the region's zone |
| `region_not_served` | `regionId` on order creation; `region` on `/user/address` | the store delivers home elsewhere in the country, but not to this region |
| `country_not_served` | `country` on `/user/address` | no home delivery of the store reaches the country |

Order creation answers a country no home delivery reaches as before: the
delivery's own refusal (`shipping_unavailable`).

A home delivery that names no carrier takes the one the store picks
(`ShippingService.resolve_home_delivery_provider`). When no carrier's rate is
in the region's zone while one delivers home elsewhere in the country, the
pick names that one, so the quote answers `shipping_region_unavailable`
rather than `shipping_unavailable`, as the weight cap names the first carrier
so an over-weight cart is refused for its weight.

Order creation refuses a region no home delivery reaches before anything
else is priced (`OrderCreateFromCartSerializer._validate_home_address`), and
a carrier that skips the region with `invalid_order_data` under
`fieldErrors.shippingProviderCode`, as for its other refusals.

### Saved addresses

A saved address is a home destination checkout prefills. A write that gives
it a country or region no home delivery reaches is refused (above); an edit
that keeps its destination is not, so an address saved before the store
stopped delivering there can still be renamed. Every response carries
`shippable` (`UserAddressSerializer.get_shippable`, one `HomeCoverage` read
per response), false for such an address: the storefront marks it and does
not let checkout choose it. Staff editing an address in the admin are not
refused (`Model.clean` applies the address rules only).

### Admin (Settings → Shipping Zones, `ShippingZoneAdmin`)

The store's own staff, under the `shipping` app's role scopes, as the rates
before them. The form is the zone's name, country and regions
(`covered_regions`, which `save_related` writes: the admin cannot edit an
M2M with its own through model), and a Rates inline. The provider page no
longer has one. The form refuses (`zone_errors`, `rate_kind_errors`):

1. a second base zone for a country;
2. a region of another country;
3. a region another zone holds (the database refuses it too);
4. a pickup-point rate on a region zone.

### Caches

`/shipping/*` and the region and country pickers read zones, so the
`shipping` and `regions_countries` surfaces declare `ShippingZone` and
`ShippingZoneRegion` in `invalidated_by`. A region set changes through
`bulk_create`, which sends no `post_save`, so
`shipping.signals.zone_regions_changed` marks both surfaces on
`m2m_changed`. Django's response cache keys on the full URL, so
`?shippable=` and `regionId` are in its key.

### Release history

Release N (expand, `shipping` `0022`-`0025`): the zone tables; one base zone
per country a store had rates for, named after the country, each rate put in
it (so nothing a store serves or charges changed); `ShippingRate.country`
made nullable and taken out of the state with its unique constraint and
index (`SeparateDatabaseAndState`), `zone` required in the state and
nullable in the database for the release still serving during the rollout.
Release N+1 (contract, `contract_of` `0025`) puts any rate that release wrote
into its country's base zone, sets `zone` NOT NULL and drops `country`, the
old constraint and its index ([`docs/migrations.md`](../migrations.md)).
