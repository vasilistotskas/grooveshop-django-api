# Order system: delivery-address rules

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 3.3 Delivery-address rules (`src/core/validators/address.py`)

Every address write is checked against the order's country row before
an order or saved address exists, so a courier never receives an
address it cannot place (prod order #316: street "1", street number
"70300", a non-numeric postcode, rejected by `ACS_Create_Voucher`).

- **Postcode**: normalised (trimmed, upper-case, whitespace collapsed to
  one space) and full-matched (ASCII) against
  `Country.postal_code_pattern`. The pattern and `postal_code_example`
  are Google's Address Data Service `zip` / first `zipex`, seeded by
  `src/country/migrations/0011_country_postal_code_format.py` and editable
  in the Country admin. A blank pattern means only "required".
- **Street** must contain a letter; **street number** must not itself
  match the country's postcode pattern (4+ characters).
- **One street line** (#641). Order creation takes `street` without
  `streetNumber` (or with it blank) as one line whose number follows the
  street, the Greek and Cypriot form, and `split_street_line` splits it,
  so the order stores both columns the ACS voucher, invoices and myDATA
  read. A wrong number sends the parcel elsewhere, which is worse than
  asking for it again, so the rules are explicit:
  1. The line is normalised: NFC, every Unicode space a plain space,
     typographic dashes `-`, a range written with spaces one number
     ("12 - 14" -> "12-14").
  2. It ends in the house number: ASCII digits, at most one letter, an
     optional second number after `-` or `/`. A number written first
     ("12 Main St") is never read.
  3. A comma is allowed only directly before the number ("Ερμού, 12");
     elsewhere ("Ερμού 12, όροφος 3", "Εγνατίας 12, 546 22") the line is
     refused.
  4. A floor or flat word (`UNIT_MARKERS`: όροφος, οροφ., ορ., ισόγειο,
     διαμέρισμα, διαμ., ημιόροφος, floor, fl., apt, apartment, unit, suite,
     flat; compared without accents or case) refuses the line: the number
     after it is not the house's ("Ερμού, όροφος 3").
  5. A word that only says "number" (`NUMBER_MARKERS`: αρ., αριθμός, Νο,
     No, nr) is dropped from the street ("Ερμού αρ. 12" -> "Ερμού", "12").
  6. The street holds no number but the day of a date that names it: 1 to
     31, with or without a Greek ordinal ending, before a month in the
     genitive ("28 Οκτωβρίου 15", "28η Οκτωβρίου 15", "Οδός 1ης Μαΐου 3").
  7. The street ends in a letter, or an abbreviation's dot after one
     ("Main St. 12").

  A line it cannot split is a 400 with `fields.street` code
  `street_number_missing`, and the errors of a split number
  (`street_number_is_postcode`) are reported on `street`: the caller sent
  no number field. A sent `streetNumber` is used as sent. The agent
  gateway sends the UCP and ACP destination's single street line this way.
- **Address line 2** (#641). `addressLine2` is optional free text for what
  the street line does not hold (a flat, a floor, a building; UCP's
  `extended_address`), kept on `Order.address_line_2` apart from the
  customer's notes. It prints under the street on the receipt and the
  order emails, and the ACS voucher carries it in `Delivery_Notes`
  ([§5.1](shipping-acs.md)). `Order.full_address` (`fullAddress`, the one
  line the order pages print) has it after the street and its number
  ("Ερμού 12, 3ος όροφος, 10563 Αθήνα"). A saved address
  (`UserAddress.address_line_2`, `/user/address`) keeps one too, so
  checkout prefills it. `Order.place` is a different field: a
  district or area (Τοποθεσία), which nothing writes at checkout; the
  receipt prints it, when set, with the city.
- **A pickup-point order is delivered to the point** (#642). For
  `shippingKind=pickup_point` the buyer's `street`, `streetNumber`, `city`
  and `zipcode` are not required and not kept: `OrderCreateFromCartSerializer.
  _deliver_to_pickup_point` stores the point's address from the carrier's
  catalogue (`ShippingCarrierInterface.address_of_pickup_point`: the BOX
  NOW locker, the exact ACS station and branch) as the order's delivery
  address, its number split from the street line by `split_street_line`
  where it splits; where it does not, `street_number` stays empty, which
  `Order.street_number` (`blank=True`), `Order.clean()` and
  `OrderWriteSerializer` allow for a pickup order only, so the admin saves
  it and a client can send back the empty number `GET` gave it. That one
  snapshot is what the receipt, the ACS voucher and the order page read,
  and the owner cannot write over it: `OrderWriteSerializer` refuses a
  change to any of `Order.SHIPPING_ADDRESS_FIELDS` (the one list of the
  delivery-address fields, so a new one is locked too) with
  `order_field_locked`; a change of point is staff work. Name, email and
  phone stay required; the
  region rule does not apply: the order keeps no region, floor, location
  type or address line 2 of the buyer's, and `ShippingChoice.of` prices a
  pickup delivery without a region, so the quote, the cart digest and the
  order agree. The
  point must be deliverable (`deliverable_pickup_point`): in the catalogue,
  active (a sync keeps a withdrawn point's row and marks it inactive) and in
  the order's country; one in another country is a 400
  `pickup_point_in_another_country`, which no resync can change. A point
  the catalogue misses or holds withdrawn (BOX NOW's widget is live data,
  the catalogue syncs daily at 02:00) is looked for once more:
  `ShippingService.pickup_point_address` resyncs that carrier's catalogue
  through its scheduled sync's own path (`refresh_pickup_points`:
  `sync_lockers`, `sync_stations` for the order's country). It runs last in
  `OrderCreateFromCartSerializer.validate`, after every cheaper refusal and
  outside any transaction or lock, and only where the store delivers that
  kind in that country (`active_rate`). At most one resync per store and
  carrier runs in `PICKUP_POINT_RESYNC_GUARD_SECONDS` (600 s); inside it a
  miss is refused at once. The guard and the order-create throttles share
  the default cache (Redis). While it is down from the start of a request,
  the fail-closed throttles (`OrderCreateThrottle`, `OrderCreateAnonThrottle`)
  refuse it before the point is read: a 503 `temporarily_unavailable` with
  no `Retry-After` (`core.middleware.strict_cache`). Only when Redis fails
  mid-request, after the throttles counted it, does the guard's `cache.add`
  raise (`REDIS_UNAVAILABLE`, logged as a warning): no resync runs, the
  point is not judged, and the order is a 503
  `pickup_point_check_unavailable` with `Retry-After` and no field error
  (`PickupPointCheckUnavailableError`, mapped in `OrderViewSet.create`;
  [error contract](../error-contract.md)). Neither creates an order. The
  request waits for the resync at most
  `PICKUP_POINT_RESYNC_TIMEOUT_SECONDS` (5 s, under the agent gateway's 10
  s): the sync runs on a daemon thread of its own, which sets the store's
  schema, logs its own failure and closes its own connection; the guard is
  taken before it starts, so a request arriving while a sync past its
  deadline still runs starts no other. Past the deadline the order is
  refused while the sync goes on, so the next order finds the point. On a
  store on BOX NOW's live network the locker the resync creates is sized
  from the store's last catalogue; one the catalogue does not name yet is
  refused as `pickup_point_size_unknown`
  ([BOX NOW](shipping-boxnow.md)). A point the resync does not hold either, a resync
  that fails or passes its deadline (logged), is a 400
  `pickup_point_unknown` on the carrier's location field (`boxnowLockerId`,
  `acsStationExternalId`); no point at all is `pickup_point_required`. The
  carriers' `validate_order_payload` apply the same rule
  (`pickup_point_errors`). The buyer's address is never used instead. An
  invoice to a pickup point names the
  company's address (`billingStreet`), since "same as delivery" would copy
  the point's. A retail receipt needs no buyer address (Law 4308/2014 art.
  12 par. 2; the former EUR 50 rule was deleted by Law 5222/2025 art. 238
  par. 2).
- **Region**: required exactly when the country has any (`Country.
  has_regions`, an `Exists(Region…)` annotation `for_list()`/
  `for_detail()` add — never a prefetch, so the storefront's country
  picker can decide whether to show the region field per country
  without an extra query); when given, it must belong to the selected
  country. A `region_id` with no such region is a 400 `region_invalid` and
  one of another country `region_country_mismatch`, both on `regionId`
  (`address_rules.region_errors`), from order creation and from the quote
  and create-payment-intent bodies, which skip it for a pickup delivery;
  never a foreign key the database refuses.
  A region the store's home deliveries skip while they reach the rest of
  its country (its shipping zones, [§5.5](shipping-zones.md)) is a 400
  `region_not_served` on `regionId` at order creation; a saved address
  there is `region_not_served` on `region` (`country_not_served` on
  `country` where no home delivery reaches the country), and one kept from
  before reads `shippable: false`.
  `src/country/migrations/0012_seed_iso_countries.py` seeds the
  full ISO 3166-1 list, most of which have no seeded regions at all —
  a region could no longer be unconditionally required at the
  serializer-field level (`UserAddressWriteSerializer.region` and
  `OrderCreateFromCartSerializer.region_id` are both optional; this
  validator is what actually enforces it, per-country).
- Applied in `OrderCreateFromCartSerializer.validate` (always; an
  unknown `country_id` is a field error), `OrderWriteSerializer` and
  `UserAddressWriteSerializer` (`address_update_errors`), and
  `Order.clean()` / `UserAddress.clean()` (`model_address_errors`, the
  admin). The last three judge the address only when the write changes
  `country`, `region`, `street`, `street_number` or `zipcode`, so an
  unrelated edit of an old record is not blocked by an address that
  predates the rules.
- Carrier code calls `normalize_postcode(value)` and
  `postcode_matches(country, value)`.
- The storefront applies the same rules inline from the same country row
  (`shared/utils/postalCode.ts`); only the normalisation and the two
  street rules are mirrored in code, and they must stay in lockstep.
- **Countries and regions are public, shared rows; what uses them is not.**
  `country` and `region` are in `SHARED_APPS` only (their tables exist
  once, in `public`; a store's `search_path` falls through to them), and
  `tenant.role_scopes.REFERENCE_DATA_APP_LABELS` gives stores `view` on
  them and nothing else. Orders, saved addresses, shipping rates and
  pay-way exclusions are in each store's own schema. So deleting one is
  refused when ANY store's rows point at it
  (`src/tenant/reference_guard.py`): the admin lists the stores on its
  confirmation page (`tenant.deletion.PublicReferenceDataDeletionMixin`)
  and a `pre_delete` receiver in `src/tenant/signals.py` refuses every other
  path. A country is also refused when a store uses one of its regions,
  because the regions go with it. Soft-deleted orders count (the foreign
  key is still there); simple-history rows do not. Deleting a row nobody
  uses works from the platform admin. Both ends of a store's use are
  found from the models' own reverse relations, hidden ones included, so
  a new model with a foreign key to `Country` is covered without a list
  to update.
