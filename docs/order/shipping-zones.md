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
  a destination resolves to one zone at most: none where its country has
  no base zone and its region is in no region zone (`zone_for` returns
  `None`, and nothing is priced there).
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
  (`OrderService._seed_estimated_delivery`). The quote and both
  order-creation paths pass the order's region for a home delivery, and the
  quote resolves the carrier once and hands it to the order
  (`CheckoutQuote.shipping_choice`), so the price charged and the carrier
  assigned come from the same zone.
- The carrier's `live_quote()` (ACS) is asked with the rate the zone
  resolved and the region, as before.
- **Without a region**, `/shipping/options` without `regionId` is priced
  by the base zone. Every surface that knows no address (`/shipping/policy`,
  `/shipping/free-shipping-info`, `/shipping/product/<id>`, the cart's
  `freeShippingProgress`) is judged at the country's standing destinations
  (`standing_destinations`, every country in two queries). Only offered
  rates count, by the gate the shippable countries use (`offered`): the
  base zone stands for the deliveries it offers, and a home delivery it
  does not offer is judged at each region zone that offers one. So a
  country served at home only in some regions, which is shippable and may
  be the one checkout preselects, advertises those regions' prices: the
  product estimate takes the cheapest, the free-shipping line their
  thresholds, and each policy row names its regions (`regionCodes`, empty
  for a base zone).
- **A zone's regions are written by `ShippingZone.set_regions`** (the
  admin writes them before the inline rates), which syncs its rates'
  `country` once for the set written; a rate is written through `save`.

### Served destinations

- **A country is shippable** (`/country?shippable=true`,
  `ShippingService.shippable_country_codes`) when any of its zones has an
  active rate, pickup included, whose carrier passes the gate a quote puts on
  it (`shipping.zones.offered`, `carrier_offers`): ACS without the store's
  credentials, or its lockers in a country with no stations, makes no country
  shippable. It costs one query for the rates and one adapter answer per
  (carrier, kind) for all its countries (`kind_enabled_countries`; ACS answers
  a set of countries with one station query), so the cart, which reads it for
  its free-shipping line, does not grow with the store's countries.
- **A region is served** when a home delivery reaches it: an active
  home-delivery rate in its resolved zone whose carrier passes the gate a
  quote puts on it (`carrier_offers`: an active provider with a registered
  adapter, supporting home delivery, enabled by the adapter for the country,
  so ACS without the store's credentials serves nothing), read in one pass
  (`HomeCoverage`). The region picker belongs to a home
  address; a pickup order keeps no region ([§3.3](creation-address.md)).
  `?shippable=true` on `GET /region` and on
  `GET /region/<alpha2>/get_regions_by_country_alpha_2` keeps the served
  regions, `false` the others; on the platform host `true` lists none.
- **A country a saved address may be in** is one some home delivery reaches
  (`HomeCoverage.reached`): `GET /country?homeDelivery=true`, the address
  book's country picker. `shippable` counts pickup points too (checkout's
  picker), so a lockers-only country is shippable but takes no saved
  address (`country_not_served`).

### Refusals (coded, [error contract](../error-contract.md))

| Code | Where | Meaning |
|---|---|---|
| `shipping_region_unavailable` | `POST /cart/quote` (`ShippingRefusal.REGION_UNAVAILABLE`) | the chosen delivery reaches the country, but its rate is not in the region's zone |
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

1. a second base zone for a country (with no column a constraint could hold,
   the form takes a transaction-scoped advisory lock on the country's zones,
   `_lock_country_zones`, so two concurrent saves cannot both pass);
2. a region of another country;
3. a region another zone holds (the database refuses it too);
4. a pickup-point rate on a region zone.

### Caches

`/shipping/*` and the region and country pickers read zones and the
carrier gate (`carrier_offers`), so the `shipping` and `regions_countries`
surfaces both declare, in `invalidated_by`:

- `ShippingZone` and `ShippingZoneRegion`;
- `ShippingProvider`: a carrier switched on or off changes what is
  shippable;
- `shipping_acs.AcsStation`: ACS offers lockers in a country only while it
  has synced stations there, so a locker-only country's `shippable`
  follows them.

A write no signal reports marks what a save would, through
`core.cache.invalidation.mark_model_stale(model)` (every surface that
declares the model):

- a region set changes through `bulk_create`, so
  `shipping.signals.zone_regions_changed` marks it on `m2m_changed`;
- a station or locker sync deactivates with an `update`.

A carrier's credentials on `Tenant` decide whether it delivers at all, as
its provider row's switch does. A change to one of
`tenant.credentials.CARRIER_CREDENTIAL_FIELDS` queues
`shipping.tasks.purge_carrier_gate_surfaces` in the store's schema
(`tenant.signals`, through `on_switch_change`), which marks the surfaces
that declare `ShippingProvider`.

Django's response cache keys on the full URL, so `?shippable=` and
`regionId` are in its key.

### Release history

Release N (expand, `shipping` `0022`-`0025`): the zone tables; one base zone
per country a store had rates for (the oldest, if it already had two), named
after the country (with its code added when another zone has that name),
each rate put in it, so nothing a store serves or charges changed. `zone` is
required in the state and nullable in the database for the release still
serving during the rollout, or back in a rollback. `ShippingRate.country`
is nullable and not editable: `ShippingRate.save` writes the zone's country
on a base zone's rate and leaves it empty on a region zone's
(`ShippingZone.rate_country_id`), and a zone's country or region-set change
rewrites its rates (`ShippingZone.save`, `shipping.signals`). So the release
before, which prices by `country` alone, finds exactly the rates it can
price. Its unique constraint and index leave the state only; in the
database the constraint still refuses that release a duplicate of a base
zone's rate.

Release N+1 (contract, `contract_of` `0025`), in this order: rerun `0024`'s
`zone_every_rate`, set `zone` NOT NULL, then drop `country`, the old
constraint and its index ([`docs/migrations.md`](../migrations.md)). The
rerun puts in its country's base zone every rate the release before wrote
during the rollout: one it inserted without a zone, and one whose `country`
its admin changed. **Where the base zone already has a rate for the same
carrier and kind, the zone's rate wins**: the other is deleted, and its id
logged at WARNING with the schema and the zone. The zone's rate is the one
this release has charged all along (a rate without a zone is not
chargeable, and a re-pointed rate was still charged in its old zone), so
what shoppers were quoted stays what they are charged; the old release's
edit there is the price to set again by hand, from the logged ids. The backfill's reverse refuses a
store whose region zones hold rates: the old shape has one rate per
`(provider, country, kind)`.
