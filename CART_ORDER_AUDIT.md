# Cart & Order Flow Audit — 2026-07 (Django + Nuxt)

Deep audit of the cart and order flows for **guest and registered** users
across payments (COD, Viva Wallet, Stripe) and shipping (ACS, BoxNow).
Every finding was validated against the live code and existing tests
before being fixed; carrier/payment changes were validated against
vendor docs. Two independent code-review passes were run over the
changeset and all their findings addressed.

**Scope:** `grooveshop-django-api/` (cart, order, pay_way, shipping,
shipping_acs, shipping_boxnow) and `grooveshop-storefront-ui-node-nuxt/`
(checkout pages, composables, cart components, generated OpenAPI types).

**Result:** 1 live production bug, 6 HIGH, and 15 MEDIUM/LOW issues fixed,
each with a regression test that fails without the fix. One product
decision left pending (BoxNow COD-at-locker), one item is documentation.

---

## P0 — Live production bug (customer DELIVERED emails were being lost)

| | |
|---|---|
| **Where** | `order/signals/handlers.py::handle_order_shipped` |
| **Symptom** | Hit 3× in prod on 2026-07-17 (orders incl. #218). |

When ACS reports a parcel jumping `PROCESSING → DELIVERED` between two
polls, `_apply_order_status_transition` bridges the missing SHIPPED step
(two `update_order_status` calls on one order instance). Both writes
register `on_commit` hooks; by the time the SHIPPED hook fires, the shared
`order.status` already reads DELIVERED, so `handle_order_shipped`'s blind
`!= SHIPPED` check re-attempted an illegal `DELIVERED → SHIPPED`
transition. The uncaught `InvalidStatusTransitionError` aborted the
remaining commit hooks — **the customer's DELIVERED email + WS
notification never sent**.

**Fix:** the defensive re-bump now only advances a `PENDING`/`PROCESSING`
order; it no-ops (with a debug log) for anything already at/past SHIPPED.
Covers both ACS and BoxNow (identical bridge). Test:
`test_handle_order_shipped_does_not_regress_delivered_order`.

---

## HIGH severity

### H1 — Any owner could `DELETE` their own order, bypassing cancellation
`order/views/order.py` — `destroy` was owner-reachable and never
overridden, so a customer could soft-delete a PENDING/PAID/SHIPPED order:
stock never restored, payment never refunded, courier voucher never
cancelled, and the order vanished from admin default querysets while the
parcel still shipped. **Fix:** `destroy` moved to `admin_only_actions`
(non-staff → 403; admin unchanged). Tests: `test_owner_cannot_delete_own_order`.

### H2 — Owner could reassign their order to another account via PATCH
`order/serializers/order.py::OrderWriteSerializer` did a raw `setattr`
mass-assign, and `user`/`pay_way`/`document_type` were writable — letting
an owner move their order (with PII) onto another account, null it to a
guest, or bypass the INVOICE cross-field rules. **Fix:** those fields are
now `read_only`. Schema + Nuxt types regenerated (surgically, no locale
churn). Test: `test_owner_cannot_reassign_order_to_another_user`.

### H3 — Stripe hosted-checkout webhook never shipped, and could un-refund
`order/signals/handlers.py::handle_stripe_checkout_completed` marked the
order paid but (unlike every other payment-success path) never dispatched
the courier task, and lacked the settled-state guard. Validated against
Stripe docs (fulfil on `checkout.session.completed`). **Fix:** added the
settled-state guard, a CANCELED-order guard (records payment, no
shipment), and the shipment dispatch. Tests: 3 in
`TestHandleStripeCheckoutCompleted`.

### H4 — Viva payment lost when the customer paid on an earlier session
Each `create_checkout_session` minted a fresh Viva `orderCode` and
**overwrote** `metadata["viva_order_code"]`; the webhook resolved orders
only by that latest value and 200'd on a miss (Viva then never retries —
confirmed via developer.viva.com). A payment on a stale tab was charged
at Viva with no record on our side. **Fix:** accumulate every issued code
in `metadata["viva_order_codes"]` (under a row lock) and resolve by any
issued code (`viva_order_code_q`) in both the webhook and the return
endpoint. Test: `test_payment_on_earlier_session_code_is_resolved`.

### H5 — BoxNow COD overcharged discounted orders at the locker
`shipping_boxnow/services.py` set `amountToBeCollected` from
`order.total_price` (pre-discount) while `invoiceValue` used
`order.paid_amount` (post loyalty discount). Validated against BoxNow docs
(the two must match; `amountToBeCollected` is the cash collected). **Fix:**
use `order.paid_amount` with a `> 0` guard, mirroring ACS. Test:
`test_cod_collects_paid_amount_not_pre_discount_total`.

### H6 — Cart Stripe payment-intent broke 100% on the contract
`cart/views/cart.py::create_payment_intent` returned `amount` as a raw
`str`, but the declared serializer + `COERCE_DECIMAL_TO_STRING=False`
make the contract a JSON **number**, which the Nuxt client validates with
`z.number()` — every call 422'd before reaching Stripe. **Fix:** route the
response through `CartPaymentIntentResponseSerializer`. Test:
`test_create_payment_intent_returns_numeric_amount`.

---

## Order-creation validation cluster (MEDIUM)

The order-create path resolved the pay-way and shipping provider by bare
PK/code with no re-validation, so a direct API call (or stale client
cache) could place orders checkout would never have offered.

- **Pickup-point requires a provider code** — otherwise the order is
  created with no provider, no shipment row, and silently never ships.
- **`ShippingProvider.is_active` re-checked for every carrier** (was
  BoxNow-only, leaving ACS unchecked).
- **Pay-way active + carrier-compatibility** — `_validate_pay_way_for_order`
  runs `PayWayService.filter_by_carrier(active(), …)`, applying the
  master switch, admin exclusions, and carrier vetoes at create time.

Tests: `test_serializer_pickup_point_requires_provider_code`,
`test_serializer_rejects_inactive_shipping_provider`,
`test_inactive_pay_way_rejected_at_creation`,
`test_carrier_excluded_pay_way_rejected_at_creation`.

> **Note:** COD-at-a-BoxNow-locker is *not* a hard code veto — it's an
> admin-configurable `PayWayShippingExclusion` (BoxNow's "pay on the go"
> is a real feature). See the pending decision below.

---

## Cart hygiene (MEDIUM/LOW)

- **Add-to-cart validates cumulative quantity** — the create serializer
  validated only the incoming delta, so `F()`-stacking could push a line
  past stock. Now validates existing + delta.
- **Non-empty guest carts are now cleaned up** — both sweep jobs filtered
  `items__isnull=True`, so a guest cart with items lingered forever. The
  30-day `cleanup_old_guest_carts` now removes them too (validated the
  abandoned-cart flow is authenticated-only, so guests are unaffected).
- **`cleanup_expired()` foot-gun removed** — a dead manager method that
  deleted *any* inactive cart, including a logged-in customer's saved
  cart with items.
- **`merge_carts` caps merged quantity at stock** (login-time analog of
  the add-to-cart check).

Tests: `test_add_rejects_when_cumulative_quantity_exceeds_stock`,
`test_cleanup_old_guest_carts_removes_old_non_empty_carts`,
`test_merge_carts_caps_overlapping_quantity_at_stock`.

---

## ACS / BoxNow robustness (MEDIUM)

- **ACS cancel dead-end fixed** — `cancel_voucher` left `voucher_no` set
  on a CANCELED shipment and `create_voucher_for_order` short-circuits on
  it, so a cancelled-before-pickup order could never be re-shipped.
  Validated against ACS docs (delete → re-create is the documented
  reissue flow). Added `reset_shipment_for_remint` (archives the old
  voucher, resets to PENDING_CREATION); the admin "Issue voucher now"
  action resets a CANCELED shipment before re-minting. Tests: 3 in
  `TestResetShipmentForRemint`.
- **ACS pickup-list single-flight mutex** — `issue_daily_pickup_list`
  runs from both the beat task and the admin "issue now" view; a race
  could double-issue the manifest at ACS or trip the unique
  `pickup_list_no` constraint. Added a Redis `cache.add` mutex mirroring
  the poll task. Test: `test_skips_when_single_flight_lock_held`.
- **BoxNow webhook silent-drop now alerts** — `process_boxnow_webhook_event`
  swallowed malformed-payload failures on a log line after the HTTP view
  already 200'd (event lost, no signal). Now pages admins via
  `alert_admins_webhook_processing_failed`. Tests in
  `tests/unit/shipping_boxnow/test_tasks.py`.
- **BoxNow webhook replay-dedup** — the HMAC covers only `data`, so the
  envelope `id` (sole dedup key) was forgeable; a captured
  `(data, datasignature)` pair could be replayed under a fresh `id`,
  re-firing customer notifications. Now dedups on a `SHA-256` of the
  signed `data` bytes too (`BoxNowParcelEvent.data_fingerprint`, migration
  `0005`). Test: `test_stores_and_dedupes_on_data_fingerprint`.

---

## Nuxt storefront (post-payment UX + cart)

- **N1 — "Back to form" trap** (`useCheckoutSubmit.ts`): after an online
  order was created (cart consumed), `backToForm` left `paymentIntentId`/
  `idempotencyKey` set, so a resubmit reused the stale intent → orphaned
  PENDING order. Now fully resets payment state, releases reservations,
  and re-syncs the cart. Test in `useCheckoutSubmit.spec.ts`.
- **N2 — Cancelled-checkout bounce** (`checkout/index.vue`): returning
  from a cancelled/failed hosted checkout hit the empty-cart middleware
  first, bouncing home with a misleading "cart empty". The middleware now
  surfaces the real reason.
- **N3 — Success-page cart wipe** (`checkout/success/[uuid].vue`):
  `cleanCartState()` fired on every visit (keyed on persistent URL query),
  so reopening the success URL wiped a newly-built cart. Now one-shot per
  order via a `localStorage` guard.
- **N4 — Quantity-selector race** (`Quantity/Selector.vue`): the debounce
  didn't serialize in-flight writes, so concurrent commits could desync
  the stepper from the cart total. Writes are now strictly serialized.

---

## Cross-cutting hardening

- **`PayWayFactory.active` default** changed from a random boolean to
  `True` — the random default silently flaked any checkout test that
  omitted `active` once order creation began rejecting inactive pay-ways.
- **Debug logging** added at non-obvious decision points (BoxNow webhook
  replay detection + dedup, order-create pay-way rejection, ACS re-mint
  reset, the P0 shipped-guard skip) so future triage is answerable from
  logs alone.

---

## Independent reviews

Two adversarial review passes were run over the changeset and every
finding verified against code and addressed:

- Orphaned Nuxt `DELETE /order` route removed (dead after H1).
- Redundant `paid_amount` `read_only_fields` entry + misleading comment
  corrected.
- Stripe settled-guard early return now persists the idempotency flag.
- Stale `amount: string` type in `useCheckout.ts` corrected.
- The `_validate_pay_way_for_order` comment corrected (BoxNow COD is
  admin-configurable, not a hard veto) + an exclusion-layer test added.

---

## Pending decision (no code change)

**BoxNow COD-at-locker ("pay on the go").** There is no
`PayWayShippingExclusion` row seeded in any environment, so COD is
currently *placeable* at a BoxNow locker in production. This is correct
if the merchant's BoxNow partner account has "pay on the go" enabled, and
wrong otherwise. **Action left pending per owner decision** — confirm the
prod `PayWayShippingExclusion` table, and add an exclusion row (or a
`BoxNowCarrier.filter_pay_ways` override) if it must be universally
blocked.

---

## Validation

- All fixes have regression tests that **fail without the fix**.
- Full suites green: order + cart + pay_way + shipping (ACS/BoxNow) on the
  Django side; the checkout composable/page/component suites on the Nuxt
  side.
- `ruff format` + `ruff check` clean; migration `0005` verified
  backwards-compatible for the Argo PreSync deploy model.
- OpenAPI schema + Nuxt types regenerated and kept in sync.
