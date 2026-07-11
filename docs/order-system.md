# Order / Payment / Shipping / Notification System

**Reference for anyone (Claude included) maintaining this surface.**
Keep this file synchronised when invariants change. Cross-references
are file paths + line numbers; the system has enough load-bearing
"don't undo this" pieces that drift here is expensive.

Last refresh: 2026-05-01 (post PR #8 + Viva PROCESSING-suppression fix). See git log for changes since.

## 1. Overview

Three orthogonal axes describe every order in the system:

| Axis | Values | Meaning |
|---|---|---|
| `Order.status` | `PENDING`, `PROCESSING`, `SHIPPED`, `DELIVERED`, `COMPLETED`, `CANCELED`, `RETURNED`, `REFUNDED` | The fulfilment state. |
| `Order.payment_status` | `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`, `REFUNDED`, `PARTIALLY_REFUNDED`, `CANCELED` | The financial state. **Decoupled from `status`** — important for COD where the parcel can be SHIPPED while payment is still PENDING. |
| `Order.pay_way.is_online_payment` | `True` / `False` | Drives which create path runs (online → online webhook completes payment, offline → COD). |

Each shipping provider (ACS, BoxNow) is a `ShippingCarrier` adapter
in `shipping/interfaces.py` registered through
`shipping/services.py::ShippingService`. The adapter pattern lets
new couriers plug in without touching the order code.

## 2. State machines

### 2.1 OrderStatus transitions (`order/services.py:1386-1404`)

```
PENDING → PROCESSING → SHIPPED → DELIVERED → COMPLETED
   │           │           │          │
   │           │           │          └──► RETURNED → REFUNDED
   │           │           └──────────► RETURNED → REFUNDED
   │           └──► CANCELED
   └──► CANCELED
```

- `OrderService.update_order_status(order, new_status)` validates
  every transition against this table.
- Terminal states: `COMPLETED`, `CANCELED`, `REFUNDED`. No exit.
- Auto-transitions wired in code:
  - `PENDING → PROCESSING` on COD voucher mint (`AcsService._advance_pending_order_to_processing`, `BoxNowService._advance_pending_order_to_processing`).
  - `PROCESSING → SHIPPED` from carrier events (`_apply_order_status_transition` in both services).
  - `SHIPPED → DELIVERED` from carrier events.
  - `DELIVERED → COMPLETED` for paid orders (`OrderService.maybe_advance_to_completed`).

### 2.2 PaymentStatus transitions

No explicit table — flips are direct assignments. Common paths:

| From → To | Trigger |
|---|---|
| `PENDING → COMPLETED` | `Order.mark_as_paid()` (called by Stripe + Viva success handlers, COD reconcile) |
| `PENDING → FAILED` | `OrderService.handle_payment_failed` (Stripe `payment_intent.payment_failed`) |
| `COMPLETED → REFUNDED` | `OrderService.refund_order()` (admin) OR `handle_stripe_charge_refunded` (full refund webhook) |
| `COMPLETED → PARTIALLY_REFUNDED` | `handle_stripe_charge_refunded` (partial refund) |
| `PENDING → CANCELED` | Viva refund webhook |
| `PENDING → COMPLETED` | `AcsService._mark_cod_order_paid_if_pending` (COD reconcile) |

## 3. Order creation paths

Three entry points in `order/services.py`:

### 3.1 `create_order` (`L79`)
- **Caller**: legacy code; some test paths.
- **Does NOT** dispatch courier task. Caller is responsible.
- Sets `language_code` via `_seed_language_code`.
- Status defaults to caller-supplied `order_data["status"]`.

### 3.2 `create_order_from_cart` (`L187`) — payment-first / online
- **Caller**: Stripe Checkout flow.
- Sets `status=PENDING`, `payment_status=<provider-returned>`.
- **Does NOT** dispatch courier task here. Online payments defer
  the dispatch to the payment-success webhook (PR #1 commit
  `59527a87` — see `project_shipping_dispatch_on_commit.md`).

### 3.3 `create_order_from_cart_offline` (`L718`) — order-first / COD / Viva
- **Caller**: Nuxt checkout for offline pay-ways + Viva redirect flow.
- Sets `status=PENDING`, `payment_status=PENDING`.
- For **COD/offline**: dispatches courier task via
  `_dispatch_shipment_creation_task` immediately at `L1130`. The
  dispatch is wrapped in `transaction.on_commit` inside
  `ShippingService.dispatch_create_shipment_task` so the worker
  only sees the order after the create transaction commits.
- For **online (Viva)**: skips the immediate dispatch — Viva's
  webhook handler does it after payment confirms.

## 4. Payment paths

### 4.1 Stripe online (`payment_intent.succeeded`)

Webhook handler: `order/signals/handlers.py::handle_stripe_payment_succeeded` (`L633`).

```
charge.succeeded → handle_stripe_payment_succeeded
                 │
                 ├── webhook_processed_{event_id} idempotency guard
                 │
                 ├── OrderService.handle_payment_succeeded(payment_intent_id)
                 │   ├── select_for_update on Order row
                 │   ├── mark_as_paid → payment_status=COMPLETED
                 │   ├── _suppress_customer_status_notifications(PROCESSING) ← PR #7
                 │   ├── update_order_status(PROCESSING)
                 │   └── _dispatch_shipment_creation_task (on_commit)
                 │
                 ├── send_order_confirmation_email.delay(order.id)  ← order_received template
                 └── notify_payment_confirmed_live.delay(order.id)
```

The `_suppress_customer_status_notifications(PROCESSING)` call is
load-bearing: without it the customer receives both an
`order_received` email AND an `order_processing` email within ms
(PR #7).

### 4.2 Stripe refund (`charge.refunded`)

Webhook handler: `handle_stripe_charge_refunded` (`L817`).

- Full refund: `payment_status=REFUNDED`, fires `order_refunded.send`.
- Partial refund: `payment_status=PARTIALLY_REFUNDED`, no signal fire.
- `Order.status` is NOT auto-mutated (REFUNDED requires RETURNED first per the canonical table; that's a business call).

### 4.3 Viva Wallet online

Webhook handler: `order/views/viva_webhook.py::_handle_payment_created` (`L372`).

Differences from the Stripe path that are easy to miss:

- **Row lock** is acquired at the entry-point (`L323`), not inside
  a service method. The whole webhook body runs inside one
  ``select_for_update`` block.
- **Idempotency** uses ``viva_webhook_{transaction_id}_{event_type_id}``
  metadata flag (Viva's analogue of Stripe's
  ``webhook_processed_{event_id}``).
- **Inline status mutation**: ``order.status = OrderStatus.PROCESSING``
  + ``order.save(update_fields=[...])``, NOT
  ``OrderService.update_order_status(...)``. The post-save signal
  still fires, but the canonical state-machine validator is
  skipped — Viva trusts that ``status==PENDING`` before the
  payment landed.
- **PR #7 PROCESSING suppression** is wired (mirrors Stripe): the
  handler calls ``OrderService._suppress_customer_status_notifications(
  order, "PROCESSING")`` immediately before the inline status flip
  so the customer doesn't get back-to-back order_received +
  order_processing emails when Viva confirms payment. Without
  this, the post-save handler would dispatch both.
- **No ``notify_payment_confirmed_live`` toast** — that's a Stripe-
  specific dispatch. Viva customers see the order_received email
  + the WS toasts that fire on later state changes (SHIPPED,
  DELIVERED).

**Post-payment redirect (fixed 2026-07-02).** Smart Checkout's
success/failure URLs are static strings configured on the source in
the Viva merchant portal — the API accepts no per-order success URL.
Viva appends ``?t=<transaction_id>&s=<order_code>&lang=..&eventId=<int32
event code>&eci=..`` (developer.viva.com → Smart Checkout
integration). The portal success URL points at the storefront's
Nitro route ``/checkout/viva-return``, which calls the public
``GET /api/v1/order/viva_return`` and 302s to
``/checkout/success/{uuid}``. The endpoint resolves ``t`` →
``payment_id`` (post-webhook) with fallback ``s`` →
``metadata.viva_order_code`` (written at session creation, so it
wins the browser-vs-webhook race). Pitfalls encoded in history:
``s`` is the 16-digit order code, NOT an ``F`` status flag, and
``eventId`` is an int32 Viva event code, NOT ``merchantTrns`` — two
earlier frontend implementations assumed otherwise and broke the
success redirect (customer landed on the homepage via the
``/checkout`` empty-cart bounce).

### 4.4 COD / PAY_ON_DELIVERY (offline)

```
1. create_order_from_cart_offline → status=PENDING, payment_status=PENDING
2. _dispatch_shipment_creation_task (on_commit) → courier voucher mints
3. AcsService._advance_pending_order_to_processing → status=PROCESSING
4. Carrier polls: shipment_state advances → status flips PROCESSING → SHIPPED → DELIVERED
5. Daily ACS COD reconcile (Mon-Fri 16:30 Athens):
   AcsService._mark_cod_order_paid_if_pending → payment_status=PENDING → COMPLETED
   Then maybe_advance_to_completed → status=DELIVERED → COMPLETED
6. Customer email + WS toast at every meaningful transition.
```

## 5. Shipping integrations

Each carrier implements `ShippingCarrierInterface` in
`shipping/interfaces.py` and registers with
`ShippingProviderRegistry`. Two carriers today:

### 5.1 ACS Courier (`shipping_acs/`)

- **REST API**, polling-based (no webhooks).
- Voucher mint: 3-phase `claim → API → persist` design in
  `AcsService.create_voucher_for_order` (`L152`). Survives
  `idle_in_transaction_session_timeout` — see
  `project_acs_voucher_orphan_prevention.md`. **TTL 300s** (PR #6).
- Polling: `poll_shipment_tracking` runs in two phases (read,
  no lock; API call, no transaction; persist with
  `select_for_update`) — survives slow ACS responses without
  losing `last_polled_at`.
- COD: `reconcile_cod_payouts` runs daily, upserts `AcsCodPayout`
  rows from the ACS COD beneficiary endpoint, and flips
  `Order.payment_status` for matched vouchers.
- HTTP 403/406 (`AcsAuthError`) is **retryable**: prod ACS returns
  sporadic transient 406s (~2% of tracking polls, self-healing —
  verified 2026-07-11); only a persistent rejection means a bad
  key/IP.
- Staleness watch: `check_stale_acs_shipments` (daily 09:00 Athens)
  emails ADMINS about non-terminal shipments with no tracking event
  for `ACS_STALE_SHIPMENT_DAYS` (default 3) days. Dedup via the
  `stale_alert_sent` claim flag, re-armed by the poller when a new
  event arrives. Dead vouchers are retired by a human via the admin
  action "Retire selected shipments" (local CANCELED — stops the
  poller; does not call `ACS_Delete_Voucher`).

### 5.2 BoxNow (`shipping_boxnow/`)

- **REST + webhook** for tracking events.
- Voucher mint: same 3-phase design as ACS.
- Webhook handler: `BoxNowService.apply_webhook_event` — idempotent on `webhook_message_id`.

### 5.3 Adding a new carrier

1. Create `shipping_<provider>/` Django app.
2. Implement `ShippingCarrierInterface`:
   - `dispatch_create_shipment_task(order)` — Celery task that mints the voucher.
   - `apply_webhook_event(event)` (if webhook-based) or polling task.
3. Register the adapter in `shipping/interfaces.py`.
4. Add `ShippingProvider` row in DB (admin or migration).
5. Implement `_apply_order_status_transition` if your carrier emits state events.
6. Wire the COD path if applicable (see ACS for the pattern).

## 6. Email + WS notification graph

### 6.1 Email triggers (final state, post PR #8)

| Event | Email task | Trigger | Idempotency flag |
|---|---|---|---|
| Order created (offline) | `send_order_confirmation_email` | `order_created` signal | `confirmation_email_sent` |
| Order created (online, pending payment) | — | (deferred to payment success) | n/a |
| Payment succeeded (online) | `send_order_confirmation_email` | webhook handler | `confirmation_email_sent` |
| Payment failed | `send_payment_failed_email` | webhook handler | `payment_failed_email_sent` |
| Status changed → DELIVERED / CANCELED / COMPLETED / REFUNDED / RETURNED | `send_order_status_update_email` | `order_status_changed` signal | `status_update_email_sent_<status>` |
| Status changed → PENDING / PROCESSING | — | (internal milestones — never a customer email) | n/a |
| Order genuinely SHIPPED (status SHIPPED **and** tracking present) | `send_shipping_notification_email` | `order_status_changed`→SHIPPED **or** `order_shipment_dispatched`, whichever completes both conditions | `shipping_notification_email_sent` |
| Refund (in-app or webhook) | `send_refund_confirmation_email` | `order_refunded` signal (PR #8) | `refund_confirmation_email_sent` |
| Invoice generated | `send_invoice_email` | from `generate_order_invoice` | `invoice_email_sent` |
| Dispute opened (staff) | `send_dispute_notification_email` | `charge.dispute.created` webhook | n/a (rare) |

All transactional emails carry `List-Unsubscribe: mailto:` headers
via `build_transactional_list_headers` (PR #4).

**The "your order has shipped" email is honest about timing.** It only
goes out once the order is *genuinely in transit* — `status == SHIPPED`
**and** a tracking number is present — never at voucher-mint. The
courier voucher mints during checkout (COD) or payment-success (online),
which sets the tracking number while the order is still PROCESSING; the
parcel only becomes SHIPPED later, when the carrier reports it moving
(ACS poll / BoxNow webhook). `send_shipping_notification_email` is
therefore dispatched from **two** events and self-gates on both
conditions: `order_shipment_dispatched` (tracking lands) and the
`order_status_changed`→SHIPPED transition. Whichever fires first finds
the other condition unmet and returns a no-op `True` *without* reserving
the `shipping_notification_email_sent` flag; the second one sends. This
also covers the admin who attaches tracking after flipping to SHIPPED
(or vice-versa). PROCESSING is never a customer email/toast — the
order-received (offline) / payment-confirmed (online) notification
already says "we're preparing your order". This is why placing a COD
order used to send three emails at once (received + "processing" +
premature "shipped"); now it sends only the confirmation.

### 6.2 Locale handling

- `Order.language_code` captured at create time
  (`OrderService._seed_language_code` reads
  `django.utils.translation.get_language()` from the active
  request locale).
- Every email task wraps render in
  `translation.override(get_order_language(order))`.

### 6.3 Customer-notification suppression on chained transitions (PR #7)

Three call paths chain `update_order_status` calls back-to-back.
Without suppression the customer would see two near-identical
emails / toasts within ms:

1. Carrier `_apply_order_status_transition` → DELIVERED → `maybe_advance_to_completed(silent_for_customer=True)` → COMPLETED.
2. `handle_payment_succeeded` → PENDING → PROCESSING (then `send_order_confirmation_email` covers the same ground).
3. `OrderService.add_tracking_info` admin path → PENDING → PROCESSING → SHIPPED.

`OrderService._suppress_customer_status_notifications(order, status)` pre-stamps two metadata flags:
- `status_update_email_sent_<status>` — short-circuits the email task's `_reserve_status_update_email`.
- `suppress_status_ws_<status>` — read by `handle_order_status_changed` to skip `notify_order_status_changed_live.delay`.

Internal state still flows: signal fires, `OrderHistory` rows logged, post-save handler runs. Only user-visible dispatches are skipped.

> Since `handle_order_status_changed` now categorically skips the
> customer email/toast for PENDING and PROCESSING (they're internal
> milestones), the PROCESSING suppression in paths 2 and 3 above is
> belt-and-suspenders. The **COMPLETED** suppression in path 1 is still
> load-bearing — COMPLETED *does* normally notify, so the
> DELIVERED→COMPLETED auto-advance must suppress it to avoid a duplicate
> right after the DELIVERED notification.

### 6.4 Live notifications (WebSocket toasts)

- `notify_order_created_live` — every order create.
- `notify_payment_confirmed_live` / `notify_payment_failed_live` — payment webhooks.
- `notify_order_status_changed_live` — meaningful status transitions only. Policy lives in `_ORDER_STATUS_COPY` (`order/notifications.py`): SHIPPED, DELIVERED, COMPLETED, CANCELED. **PENDING and PROCESSING are intentionally absent** — they're covered by the order-created / payment-confirmed toasts, so surfacing them again would be redundant.
- `notify_order_refunded_live` — refund signal.

There is no standalone "tracking available" toast: it fired at
voucher-mint (the same premature moment as the old shipped email) and
was redundant with the SHIPPED toast, so it was removed alongside the
email fix.

All WS notifications go through `notification.consumers.NotificationConsumer` and require auth; **guest orders silently get no live notification** (the email IS sent for guests since it goes to `order.email`).

## 7. Cancellation + refund flows

### 7.1 Cancel paths

- **Customer**: `POST /api/v1/orders/{id}/cancel/` → `OrderService.cancel_order(order, reason, refund_payment=True)`.
- **Admin**: same `cancel_order` via Django admin action / unfold detail action.
- **Auto**: `auto_cancel_stuck_pending_orders` Celery beat — cancels online orders stuck in PENDING for >24h.

`cancel_order`:
1. Locks order row.
2. Releases stock + reservations (`StockManager.increment_stock`, `release_reservation`).
3. Sets `status=CANCELED`, records `metadata['cancellation']`.
4. Cascades to courier voucher via `ShippingService.cancel_shipment` (PR #2 H). Records dispatch outcome on metadata. Carrier rejection (e.g., voucher already in pickup list) is swallowed and logged.
5. Optional refund via `refund_order` (when `refund_payment=True` AND `is_paid`).

**Don't bypass**: do not call `order.save(update_fields=['status'])` directly to cancel — you'll skip stock release + email + history. Always go through `OrderService.cancel_order`.

### 7.2 Refund paths

- **Admin**: `POST /api/v1/orders/{id}/refund_order/` → `OrderService.refund_order(amount=None|Money, reason)`.
- **Stripe dashboard**: webhook `charge.refunded` → `handle_stripe_charge_refunded`.

Both fire `order_refunded.send` → `handle_order_refunded` → email + WS notification. Single boolean `refund_confirmation_email_sent` flag dedupes between paths (PR #8).

## 8. Critical invariants

These exist for a reason. Don't undo any without first re-reading
the linked memory note or the originating PR's commit message.

| Invariant | Anchor |
|---|---|
| Backend uses `psycopg` pool with `CONN_MAX_AGE=0` under ASGI | `project_db_pool.md` |
| `ShippingService.dispatch_create_shipment_task` wraps in `transaction.on_commit` | `project_shipping_dispatch_on_commit.md` |
| ACS voucher mint uses 3-phase claim → API → persist with 300s TTL | `project_acs_voucher_orphan_prevention.md` |
| `Order.objects.filter(pk=...).values(...).first()` — NOT `refresh_from_db(fields=...)` | `project_order_state_machine_invariants.md` |
| `_suppress_customer_status_notifications` on chained transitions | `project_order_state_machine_invariants.md` |
| Webhook payment_status writes never regress a SETTLED state (`SETTLED_PAYMENT_STATUSES` = COMPLETED/REFUNDED/PARTIALLY_REFUNDED/CANCELED). Stripe/Viva events are unordered + may duplicate, so `handle_payment_failed`/`_handle_payment_failed` skip when already settled, and `handle_payment_succeeded`/`_handle_payment_created` skip when already REFUNDED/PARTIALLY_REFUNDED/CANCELED. Reversal (COMPLETED→REFUNDED) is left intact. | `order/services.py` `SETTLED_PAYMENT_STATUSES` |
| The "shipped" email/toast fires only at genuine SHIPPED (status==SHIPPED **and** tracking present), never at voucher-mint; PROCESSING is internal-only (no customer email/toast). `send_shipping_notification_email` self-gates. | §6.1 above |
| Admin-WYSIWYG fields in emails render `\|safe` (.html) / `unescape(strip_tags())`+`\|safe` (.txt — Django autoescapes .txt too) | §6.1 above |
| ACS COD numeric fields use Greek-locale (comma decimal) | `project_acs_cod_locale.md` |
| Don't import from `'#shared/...'` in app/ or server/ | `feedback_no_shared_imports.md` |
| Don't override generated Zod / OpenAPI types in Nuxt | `feedback_no_local_schema_overrides.md` |
| OpenAPI regen workflow after Django serializer changes | `project_schema_regen.md` |

## 9. Common task playbook

### 9.1 Adding a new order email

1. Create the template under `core/templates/emails/order/`.
2. Add a Celery task in `order/tasks.py` mirroring `send_payment_failed_email`:
   - Idempotency reservation helper (`_reserve_<task>_email`).
   - `translation.override(get_order_language(order))` block.
   - `EmailMultiAlternatives(...)` with `headers=build_transactional_list_headers(list_id="<kind>")`.
   - Catch exceptions; on permanent failure, release the reservation.
3. Wire from a signal handler in `order/signals/handlers.py` via `transaction.on_commit`.
4. Add a regression test in `tests/integration/order/test_signals.py`.

### 9.2 Adding a new order status

1. Add the value to `OrderStatus` in `order/enum/status.py`.
2. Update the transition table in `OrderService.update_order_status`.
3. Add a Greek translation in `locale/el/django.po`, run `compilemessages`.
4. Add the matching `_ORDER_STATUS_COPY` entry in `order/notifications.py` for the WS toast.
5. Create email templates under `core/templates/emails/order/order_<status>.{html,txt}`.
6. Add a state-machine test in `tests/integration/order/test_state_machine.py`.

### 9.3 Surfacing a new field on the order detail API

1. Add the field on `OrderSerializer` (list shape) or `OrderDetailSerializer` (detail-only) in `order/serializers/order.py`. Use `extend_schema_field` so spectacular emits it.
2. `uv run python manage.py spectacular --color --file schema.yml`.
3. In Nuxt: `pnpm generate:schema && pnpm openapi-ts`.
4. Type-safe consumption in `app/pages/account/orders/[id].vue`.

### 9.4 Reconciling state after a manual prod fix

If you ever touch order metadata or status directly via shell
(`order.save(update_fields=[...])`), you've bypassed signals.
Recover by:
1. Note exactly which fields you changed.
2. If status was changed: roll it back to `PENDING` and call
   `OrderService.cancel_order(...)` so the canonical path fires.
3. For voucher mint mishaps, follow the order-47 recovery script
   in `commit 05208050`.

## 10. Audit history (PRs #1–#8)

All landed 2026-04-30 → 2026-05-01.

| PR | Theme | Scope |
|---|---|---|
| 1 | Race conditions | COD voucher-mint advance, payment handler row locks, on_commit regression test |
| 2 | State machine completion | Cancel cascade to courier, charge.refunded webhook, COD reconcile flips payment_status, DELIVERED→COMPLETED auto |
| 3 | UX polish | PaymentStatus translations + display, COD alert suppression, cancellation surface, language_code capture |
| 4 | Email gaps | Shipping notification email wiring, List-Unsubscribe transactional headers |
| 5 | Audit trail | HistoricalRecords on shipments, is_online_payment list serializer, dispute_notification templates |
| 6 | Concurrency hardening | Voucher-mint TTL 90→300s, ACS DELIVERED→COMPLETED tests |
| 7 | Notification dedup | Suppress duplicate customer emails + WS toasts on chained transitions |
| 8 | Last gap | Refund confirmation email |

For the *why* behind each item, read the commit message — they're
written specifically to survive a `git log --grep="^fix"` browse
six months from now.
