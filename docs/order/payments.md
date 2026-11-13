# Order system: payment paths

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 4. Payment paths

**Per-tenant payment identity.** Every tenant runs its OWN payment
accounts:

- **Stripe — separate accounts, not Connect.** dj-stripe lives in
  TENANT_APPS (per-schema tables); `StripePaymentProvider` resolves the
  key via `tenant.credentials.stripe_credentials()` and passes it per
  call (`api_key=`, never the `stripe.api_key` module global). A tenant
  with no `stripe_secret_key` simply has Stripe unavailable — there is
  no platform-account concept or fallback of any kind (the "platform"
  account was always just webside's own account). Webhooks are
  HOST-ROUTED per tenant:
  `https://api.<tenant-domain>/stripe/webhook/<uuid>/` — django-tenants
  selects the schema, then dj-stripe's UUID view verifies against the
  per-schema `WebhookEndpoint` row secret. Provisioning (APIKey row +
  Stripe endpoint incl. secret): `manage.py bootstrap_stripe
  --schema=<schema>` (idempotent; `--rotate-endpoint` to re-mint). Every
  tenant, including webside, backfills its own `stripe_secret_key` +
  `stripe_publishable_key` on the Tenant row before running
  `bootstrap_stripe`.
- **Viva Wallet** — all credentials incl. `source_code` and `live_mode`
  per-tenant (no settings fallback). The webhook route on the tenant's own
  API host resolves the tenant BEFORE the GET verification challenge,
  so per-tenant verification keys work with no view changes; POST
  delivery still resolves the order by payload as source of truth.
- **BoxNow** — HMAC secret per-tenant (`box_now_webhook_secret`),
  resolved from the signed `parcelId` before verification. The API
  environment (stage or production) is per-tenant too (`box_now_live_mode`,
  [§5.2](shipping-boxnow.md)).
- **Availability gate** — `PayWayService.is_provider_configured()`
  hides credential-less providers from the shopper pay-way list AND
  refuses checkout-session/SPT creation for them.

### 4.1 Stripe online (`payment_intent.succeeded`)

Webhook handler:
`src/order/signals/handlers.py::handle_stripe_payment_succeeded`.

```
payment_intent.succeeded → handle_stripe_payment_succeeded
                 │
                 ├── webhook_processed_{event_id} idempotency guard
                 │
                 ├── OrderService.handle_payment_succeeded(payment_intent_id, captured=amount_received)
                 │   ├── select_for_update on Order row
                 │   ├── PaymentLedger.record_charge (CHARGE row, whatever state the order is in)
                 │   ├── mark_as_paid → payment_status=COMPLETED
                 │   ├── suppress_customer_status_notifications(PROCESSING) ← PR #7
                 │   ├── update_order_status(PROCESSING)
                 │   └── _dispatch_shipment_creation_task (on_commit)
                 │
                 │   └── returns PaymentEventOutcome(order, previous_payment_status, applied)
                 │
                 │   only when outcome.applied:
                 ├── OrderHistory PAYMENT row (previous = the real prior status)
                 ├── send_order_confirmation_email (on_commit)  ← order_payment_confirmed template
                 └── notify_payment_confirmed_live (on_commit, logged-in users only)
```

**The collected amount must be the order's total.** `handle_payment_succeeded`
(and the `checkout.session.completed` handler) compare what Stripe collected
with `order.calculate_order_total_amount()` (currency, and amount within
`PAYMENT_AMOUNT_TOLERANCE`) before `mark_as_paid`. A charge that does not
match is booked on the ledger (the money moved) but does not pay the order:
`OrderService.flag_payment_mismatch` stamps `AMOUNT_MISMATCH_FLAG` (which
also keeps `auto_cancel_stuck_pending_orders` away from it), notes the order
and mails `ops.payment_mismatch`; the Viva amount/currency refusals
use the same function.

**A poll never writes `COMPLETED` itself.** `GET /order/{id}/payment_status`
(`OrderService.get_payment_status`) does not write the provider's status onto
the row. A polled COMPLETED settles through the same
`handle_payment_succeeded` (and announces through
`announce_stripe_payment_applied`, shared with the webhook and reached by the
`order_stripe_payment_polled` signal), only for a Stripe
intent no gift-card purchase holds; any other provider's payment is settled by
its webhook alone. `PayWayService.check_payment_status` (a second, caller-less
polling writer) was removed.

`applied` is False for a stale event against a settled payment, a
repeat of one already applied, and a charge on a canceled order
(`record_payment_after_cancel` books it and alerts ops): none of them
gets a history row or a customer email. `handle_payment_failed` returns
the same outcome, so a late failure never tells a customer who paid
that their payment failed.

The `suppress_customer_status_notifications(PROCESSING)` call is
load-bearing: without it the customer receives both an
`order_payment_confirmed` email AND an `order_processing` email within ms
(PR #7).

**The charge is recorded on the payment ledger** ([§7.3](payment-ledger.md)):
`captured` is the
event's `amount_received` (what Stripe collected, not what was asked for),
the ledger key is the PaymentIntent id, and the row is written before any
guard, so a stale event, a repeat, a settled order or a charge on a CANCELED
order still leaves its CHARGE (for the last, that row is what lets staff
refund it in-app). `checkout.session.completed` records the same charge from
the session's `amount_total` under the session's PaymentIntent id, so the
`payment_intent.succeeded` that follows adds nothing. An event without
`amount_received` is dropped as malformed, like one without an id.

### 4.2a Stripe fee (`charge.succeeded`, `charge.updated`)

`src/order/payment_fees.py`. A Stripe fee is the `fee` of the charge's balance
transaction, which dj-stripe syncs with the charge
(`Charge.sync_from_stripe_data`, `Charge.balance_transaction`).
`handle_stripe_charge_fee` writes it onto the CHARGE row keyed on the
PaymentIntent id; `charge.updated` is as much a fee event as
`charge.succeeded`, because a slower payment method attaches its balance
transaction later. Stripe orders its events freely, so when the charge's events
came first and found no ledger row, `handle_stripe_payment_succeeded` calls
`record_stripe_fee_for_intent` after it books the payment and reads the charge
itself (a Stripe outage there is logged and swallowed: the payment is booked).
A charge with no ledger row costs no Stripe call, and a fee settled in another
currency than the payment is left unreported (`PaymentLedger.record_fee`
converts nothing).

### 4.2 Stripe refund (`refund.created`, `refund.updated`)

Webhook handler: `handle_stripe_refund_made` (`src/order/signals/handlers.py`),
registered for both events, which carry the Refund object (Stripe:
`charge.refunded` says to listen to `refund.created` for the refund itself,
docs.stripe.com/refunds).

An order's refund is booked ONCE PER STRIPE REFUND ID (`re_...`), the key the
in-app refund, the reconciliation lookup (`find_refunds`, `confirm_refund`) and
`refund.failed` all use. So a refund is one row however it arrives,
reconciliation sees the row a webhook booked, and a later reversal finds the
row to free its credit.

- Which statuses count is read from Stripe's CURRENT state of the refund
  (`retrieve_refund` with the store's credentials), never from the event's
  snapshot: `succeeded` and `pending` are a refund made
  (`StripePaymentProvider._ACCEPTED_REFUND_STATUSES`); `failed`, `canceled` and
  `requires_action` book nothing. `handle_stripe_refund_failed` reads the same
  state, so the two handlers cannot disagree, and a `refund.created(pending)`
  processed after the failure books nothing. A Stripe error on the read
  propagates, so Stripe redelivers the event.
- The row is keyed on the refund id and `PaymentLedger.record_refund` derives
  `payment_status` ([§7.3](payment-ledger.md)). A refund this app made is
  already on the ledger under that id, so its event adds nothing; the same
  holds for a redelivery and for `refund.updated` after `refund.created`. A
  dashboard refund next to an in-app one counts each once, and the order the
  events arrive in does not matter.
- An unclaimed refund settles the PENDING attempt that matches its charge and
  amount, whatever its age ([§7.2](refund-execution.md)).
- A refund of a payment the ledger does not hold yet is REFUSED (an error, so
  Stripe redelivers it): the payment's own event may still be on its way.
- Whatever the ledger records fires `order_refunded.send` with that row's
  `amount`, partial refunds included ([§7.2](refund-execution.md)).
- `Order.status` is NOT auto-mutated (REFUNDED requires RETURNED first per the
  canonical table; that's a business call).
- **`charge.refunded`** (`handle_stripe_charge_refunded`) books no order
  refund: its figure is cumulative and names no refund, so it is used only to
  void a purchased gift card once the refunds reach the purchase amount. The
  Stripe webhook endpoint must deliver `refund.created` and `refund.updated`.

#### 4.2.0 A Stripe refund that FAILS after it was accepted (`refund.failed`, `refund.updated`, `charge.refund.updated`)

Webhook handler: `handle_stripe_refund_failed`
(`src/order/signals/handlers.py`), registered for all three event types, which
carry the Refund object; only a refund Stripe NOW reports `failed` or
`canceled` acts (read with `StripePaymentProvider.retrieve_refund`, never from
the event; Stripe: a refund is `succeeded` or `pending` when accepted and can
move to `failed` later, the money returning to the merchant's Stripe balance,
and the merchant must pay the customer another way: docs.stripe.com/refunds).

- The ledger counted the refund when it was accepted, so the handler writes ONE
  `REFUND_REVERSAL` row (`PaymentLedger.record_refund_reversal`) keyed on the
  refund id: the three event types and redeliveries book it once. It is capped
  at what the ledger holds as refunded on that charge, and a refund the ledger
  never counted (nothing to reverse) is logged and ignored. The refund row it
  reverses is found by the same refund id (`handle_stripe_refund_made` books
  under it), so the credit it would have owed is freed
  (`credit_allocation.free_reversed`).
- `refunded`, `refunded_on`, `balances` and `left_on` subtract a reversal, so
  the money is refundable again, and `refund_status` derives `payment_status`
  again (COMPLETED when every refund was reversed, PARTIALLY_REFUNDED when some
  stand).
- Staff are told twice: a NOTE on the order and the `refund_failed` operational
  email ([§5.4](shipping.md)).
- **What is NOT taken back, on purpose.** The refund fired `order_refunded`
  when it was accepted, and its consequences stand: the gift-card credit and
  the points released stay (the customer is still owed the money, and taking
  value back from them would punish a provider failure), the customer email
  stays (a second email would only confuse; staff are told to pay the customer
  another way or refund again once the cause is cleared), and the retail credit
  document stays (it records what the merchant owes). A repeat refund after a
  failure issues another credit document, which `create_pending_credits`
  refuses when it would exceed the receipt (a NOTE says so): that case needs
  the accountant, it is not resolved automatically.
- Stripe does not order its events, and the handlers do not trust their
  snapshots: both read the refund's current status, so a `refund.failed`
  processed before the refund was booked finds no row to reverse (logged and
  ignored), and the `refund.created` that follows reads `failed` and books
  nothing.

#### 4.2.1 Stripe dispute (`charge.dispute.created`, `charge.dispute.closed`)

Webhook handlers: `handle_stripe_dispute_created` and
`handle_stripe_dispute_closed` (`src/order/signals/handlers.py`). Both find the
order by the dispute's `payment_intent` and are guarded by the
`webhook_processed_<event id>` metadata flag.

- **Opened** flags the order (`metadata["disputed"]`, `dispute_id`,
  `dispute_status`), writes a NOTE and emails staff
  (`send_dispute_notification_email`). The ledger is untouched: until the
  dispute closes the money is still the merchant's to defend.
- **Closed, `status == "lost"`**: the money is gone.
  `PaymentLedger.record_chargeback` records ONE CHARGEBACK row against the
  order's CHARGE (read from the provider by `ensure_charge` when the ledger
  lacks it), keyed on the dispute id, so a repeat adds nothing. The row lowers
  `refundable`, so staff cannot refund money already taken back. The order gets
  a red `CHARGEBACK` history entry ("Chargeback €X";
  `ORDER_HISTORY_CHANGE_TYPE_VARIANT`, shown in the order admin's history tab)
  and staff get the same dispute email with `lost=True`. The row then awaits a
  staff decision on the goods (`ChargebackDecision`, see "Chargebacks" under
  the credit documents): whether the sale was reversed decides if the receipt
  is credited.
- **Closed, any other status** (`won`; `warning_closed`, an inquiry that never
  became a dispute): clears `metadata["disputed"]` and leaves a NOTE. No row.
- **A refund is refused while a dispute is open.** Stripe: "you can't issue a
  refund on a disputed charge until your customer's card issuer decides in your
  favor" (docs.stripe.com/disputes/withdrawing). `OrderService.refund_order`
  raises `PaymentError` when `PaymentLedger.dispute_open` holds (flagged, no
  CHARGEBACK row for that dispute id yet). An inquiry (`dispute_status`
  `warning_*`) is not refused: Stripe lets the merchant refund one to resolve
  it. An inquiry that escalates to a dispute is not tracked (there is no
  `charge.dispute.updated` receiver), so Stripe's own refusal is what staff see
  then. `cancel_order` still cancels and reports the refund as failed.
- **`payment_status` does not move** on a lost dispute, and **`order_refunded`
  does not fire**: nothing was paid back, so there is no customer email, no
  Meta `Refund` event and no gift-card release. `refunded` and `refunded_on`
  count REFUND rows only, so the refund arithmetic (§4.2) is unchanged; a
  partly refunded order that is then charged back stays PARTIALLY_REFUNDED.
- **Loyalty**: `order_charged_back` (sent once per new CHARGEBACK row) reverses
  the earned points once refunds plus chargebacks cover everything received
  (`PaymentLedger.fully_returned`, the same decision the refund path takes;
  [§7.3](payment-ledger.md)). Unlike a refund or cancellation, it does **not**
  give back the points the customer redeemed on the order: the customer took
  the money back through their bank, so what they spent stays spent (owner
  decision 2026-10-17; `LoyaltyService.reverse_order_points` skips the restore
  when the order has a CHARGEBACK row). Staff can re-grant by hand. A refund or
  a cancel takes back ALL the points the order earned even when the customer
  has spent them since, when `LOYALTY_REFUND_ALLOWS_NEGATIVE_BALANCE` is on
  (the default): the balance goes negative, later earnings pay it off, and a
  negative balance blocks redeeming (`redeem_points` needs
  `balance >= points`). Off, the reversal stops at zero and the spent points
  survive (earn, spend, refund: the loop the default closes).

#### Dispute lifecycle per provider

| | Stripe | Viva Wallet |
|---|---|---|
| Opened | `charge.dispute.created` | 2054 *Account Transaction Created* whose sale reads `M` / `MA` / `MI` |
| Lost | `charge.dispute.closed`, `status == "lost"` | 2054 whose sale reads `ML`, or a 1797 refund claim (types 13 / 46 / 83 / 90) |
| Won | `charge.dispute.closed`, `won` | 2054 whose sale reads `MW` |
| Ledger key of the CHARGEBACK | the dispute id | the CHARGE's own Viva transaction id (the original sale) |
| Refund while open | refused (documented) | refused (a disputed payment cannot be refunded by the merchant) |

Viva has no dispute webhook, and does not document whether a chargeback arrives
as a 1797, so a claim type on the 1797 we already receive is handled and the
dispute is otherwise followed through its sale's `StatusId`
(developer.viva.com/integration-reference/response-codes/, StatusId table: `M`
"cardholder has disputed the transaction", `MA` "Dispute Awaiting Response",
`MI` "Dispute in Progress", `ML` "Dispute Lost", `MW` "Dispute Won"; `MS`
"Suspected Dispute" is not acted on). The 2054 payload has no `OrderCode` and
Viva documents no subtype for the chargeback debit (SubTypeId 2 `FeeChargeBack`
is the fee for losing, 162/163 reserve and release funds), so **no SubTypeId is
interpreted**: the handler (`_handle_account_transaction_event`) reads
`EventData.SaleTransactionId`, finds the store by the ledger (a Viva CHARGE
with that id, or an order paid by it: database lookups only, no Viva call for a
clearance, fee or payout that is none of ours), authenticates by Retrieve
Transaction for that sale, matches the order by the retrieved `orderCode` and
acts on the retrieved status. Anything else (no sale id, an unknown sale,
another status) is acknowledged and ignored. Each 2054 is deduplicated on
`VivaWebhookEvent` (its own `TransactionId` and 2054). The chargeback amount is
the retrieved sale's amount (absolute), because Viva documents no disputed
amount; the ledger caps it at what the payment still holds (see below). Each
store must subscribe the 2054 webhook in its Viva dashboard (Settings > API
Access > Webhooks > Create Webhook, one subscription per event type). A 2054
(or a 1797 claim) for a suspended or inactive tenant answers 503, exactly as
the payment path does, so Viva redelivers (hourly, 24 attempts): acknowledging
it would lose the chargeback for good.

**The chargeback is capped at what the payment still holds.**
`PaymentLedger.record_chargeback` records at most captured minus refunds minus
earlier chargebacks (`PaymentLedger.left_on`), whichever provider reports it,
and logs a warning when it caps (Stripe's amount should fit; Viva's whole-sale
amount does not once the sale was partly refunded). A capped entry says so in
the red history line ("Chargeback €70.00 (disputed €100.00, €30.00 already
returned)"). When nothing is left no row is written, but the history entry
("Chargeback of €X not recorded: nothing is left on the payment") and the staff
email still go out, once (`order.disputes.announce_chargeback`; Viva remembers
it in `metadata["dispute_resolved_id"]`, because each wallet movement of a
dispute is an event of its own).

#### Gift-card purchases are reversed too

A gift-card purchase is not an order, so the order lookups above do not
find it. Each reversal of its payment reaches
`GiftCardService.handle_purchase_reversal`: a Viva refund (once the refunds
reach the purchase amount) or refund claim (types 13 / 46 / 83 / 90, whatever
amount it names), a Viva 2054 whose sale reads `ML`, a Stripe `charge.refunded`
(cumulative; voids once it reaches the purchase amount) and a lost
`charge.dispute.closed`. It voids what is left on every card the purchase
issued whether or not the card was touched, disables it, and records what was
already spent in `GiftCardPurchase.unrecovered_amount` with an ERROR log and a
`gift_card_reversal` ops alert. An OPENED dispute (Viva `M`/`MA`/`MI`, Stripe
`charge.dispute.created`) only alerts: the money is still the merchant's to
defend. The Viva dispute path finds the purchase by `provider_transaction_id`
(the TransactionId the 1796 completed it with; a purchase completed before
`giftcard 0008` has none and is not found).

### 4.3 Viva Wallet online

Webhook handler: `src/order/views/viva_webhook.py::_handle_payment_created`.

Differences from the Stripe path that are easy to miss:

- **Row lock** is acquired in the view (`_process_event_in_tenant`), not inside
  a service method. The whole webhook body runs inside one
  ``select_for_update`` block.
- **Idempotency** uses the ``VivaWebhookEvent`` table
  (``src/order/models/viva_webhook_event.py``), unique on
  ``(transaction_id, event_type_id)`` and written last in the same
  transaction — not a metadata flag like Stripe's
  ``webhook_processed_{event_id}``.
- **Inline status mutation**: ``order.status = OrderStatus.PROCESSING``
  + ``order.save(update_fields=[...])``, NOT
  ``OrderService.update_order_status(...)``. The post-save signal
  still fires, but the canonical state-machine validator is
  skipped — Viva trusts that ``status==PENDING`` before the
  payment landed.
- **PR #7 PROCESSING suppression** is wired (mirrors Stripe): the
  handler calls ``OrderService.suppress_customer_status_notifications(
  order, "PROCESSING")`` immediately before the inline status flip
  so the customer doesn't get back-to-back payment-confirmed +
  order_processing emails when Viva confirms payment. Without
  this, the post-save handler would dispatch both.
- **No ``notify_payment_confirmed_live`` toast** — that's a Stripe-
  specific dispatch. Viva customers see the payment-confirmed email
  + the WS toasts that fire on later state changes (SHIPPED,
  DELIVERED).
- **The charge is recorded on the payment ledger** ([§7.3](payment-ledger.md))
  once the
  transaction is verified (it belongs to the order, the currency and
  amount match, status `F`): the verified amount, keyed on the
  transaction id, before the canceled-order and settled guards. A second
  payment under another of the order's checkout codes is a second CHARGE
  row (`Order.payment_id` still names only the latest).

**Settlement is bound to the thing paid for.** A 1796 settles an order only
when the verified transaction carries one of the order's own codes, states its
currency and amount, and both are the order's; a transaction that states
neither (or whose amount is unreadable) is refused and flagged, not settled on
status alone. The gift-card branch (`_gift_card_payment_problem`) makes the
same check against the purchase's own order code, currency and amount.

**Refund webhook (1797, `_handle_reversal_created`).** The event's
`TransactionId` is the REFUND, a transaction of its own; `ParentId` is the
payment it reverses
([Viva](https://developer.viva.com/webhooks-for-payments/transaction-reversal-created/)).
Per Viva's
[status and type tables](https://developer.viva.com/integration-reference/response-codes/)
that transaction reads `statusId` `F` (`R`, "fully or partially refunded",
is the status of the ORIGINAL payment), a refund or void
`transactionTypeId` (`VIVA_REFUND_TRANSACTION_TYPE_IDS`; the "Refund
Claimed" types, `VIVA_REFUND_CLAIMED_TRANSACTION_TYPE_IDS`, are chargebacks
and are booked as such, see *Dispute lifecycle per provider*) and a
**negative** `amount`.
The handler used to wait for the refund transaction itself to read
REFUNDED, which Viva never reports, so every real 1797 was skipped — and
recorded as processed, so Viva never retried. Nothing is read from the
unauthenticated body except `ParentId`:

1. `_verify_viva_reversal` retrieves the refund transaction (an error raises
   → 500 → Viva retries, and the multi-tenant candidate loop moves on) and
   requires one of the order's issued order codes on it, `F`, a refund or
   void type and a negative amount; the refunded amount is `-amount`. The
   gift-card purchase path (`_process_gift_card_purchase_event`) goes
   through the same verifier with the purchase's own order code. The card
   is the whole purchase, so each verified refund is added to
   `GiftCardPurchase.refunded_amount` and the card is voided once the
   refunds reach the purchase amount (a refund id counts once: the
   `VivaWebhookEvent` row is its idempotency key); a part refund leaves the
   value the buyer still owns alone.
2. The refund is attached to the payment `ParentId` names, and that payment
   must be one of the order's own CHARGE rows on the ledger
   ([§7.3](payment-ledger.md)): an order
   paid twice under two of its checkout codes holds two, and each refund goes
   against the one it reverses. Retrieve Transaction documents no parent id,
   so `ParentId` can only refuse, never grant: the payment it points at comes
   from our own rows. For an order whose capture was never recorded, a
   `ParentId` equal to the order's own `payment_id` is read from Viva once and
   recorded (unreadable → 500, Viva retries); anything else is skipped.
3. `PaymentLedger.record_refund` keys the REFUND row on the refund
   transaction id. A refund already there is skipped: Viva notifies every
   refund, including the one `refund_order` has just made, and redelivers
   events. This is the unique constraint doing the work, behind
   `VivaWebhookEvent`'s own guard.
4. The row's amount is `-amount` of the verified refund; `payment_status`
   is derived from the ledger's sums by `record_refund`
   ([§7.3](payment-ledger.md)). Nothing is
   read from Viva about what was captured: the ledger holds it. There is no
   "is the order paid" status guard any more: a verified refund of a recorded
   payment is a fact whatever the status says, and an unpaid order has no
   CHARGE for it to name.
5. `order_refunded` fires with `amount` = this refund
   ([§7.2](refund-execution.md));
   `Order.status` is not touched.

The dispatcher stores the handler's real outcome in
`VivaWebhookEvent.outcome`: every skip above (and a 1798 that changed
nothing) is `skipped`, a booked refund `processed`. Ops: each tenant's Viva
account needs the *Transaction Reversal Created* webhook subscribed
(Settings → API Access → Webhooks). Production had received no 1797 by
2026-10-10, so check the subscription before assuming that a refund made in
Viva's dashboard reached the order. Dispute tracking needs a second
subscription, *Account Transaction Created* (2054), in the same place. The
provider fee needs a third, *Transaction Price Calculated* (1799):
`_handle_transaction_price_calculated` retrieves the transaction like every
other event (a forged body, or another store's transaction, records nothing),
requires one of the order's own order codes, and writes
`EventData.TotalCommission` (interchange + ISV fee + Viva's fee, major units of
`CurrencyCode`) as `fee` on the ledger row whose `provider_reference` is
`EventData.TransactionId`. `Interchange` is not read: Viva's own samples
disagree on its sign. A payment the ledger lacks, a missing or negative
commission or an unknown currency is skipped (`outcome` `skipped`), never
booked as zero. A store that does not subscribe 1799 simply shows its Viva
payments as "fee not reported".

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
``metadata.viva_order_codes`` (every code the order issued, appended at
session creation, so it wins the browser-vs-webhook race). Pitfalls encoded in
history:
``s`` is the 16-digit order code, NOT an ``F`` status flag, and
``eventId`` is an int32 Viva event code, NOT ``merchantTrns`` — two
earlier frontend implementations assumed otherwise and broke the
success redirect (customer landed on the homepage via the
``/checkout`` empty-cart bounce).

The 200 answer is a union published as a `oneOf` discriminated on
`kind`: `order` carries `id`, `uuid`, `status`, `paymentStatus`;
`gift_card_purchase` (a purchase shares the static return URL)
carries `purchaseUuid`, `purchaseStatus`. Every field of a branch is
required, so the storefront's generated schema needs no local
rebuild of the union.

### 4.4 COD / PAY_ON_DELIVERY (offline)

```
1. create_order_from_cart_offline → status=PENDING, payment_status=PENDING
2. _dispatch_shipment_creation_task (on_commit) → courier voucher mints
3. AcsService._advance_pending_order_to_processing → status=PROCESSING
4. Carrier polls: shipment_state advances → status flips PROCESSING → SHIPPED → DELIVERED
5. Daily ACS COD reconcile (02:30 Athens, queries yesterday's payouts):
   AcsService._mark_cod_order_paid_if_pending → payment_status=PENDING → COMPLETED
   Then maybe_advance_to_completed → status=DELIVERED → COMPLETED
   (and the hourly complete_paid_delivered_orders sweep catches any
   DELIVERED + COMPLETED row that step missed).
   Always silent for customers (email + toast suppressed; the shopper
   paid in person and already got the DELIVERED notification) — both
   the beat task and the backfill command pass silent_for_customer.
   The collection goes on the payment ledger as a COD_COLLECTION row
   (provider `acs_cod`, reference the voucher, amount the voucher's
   `cod_amount`; BoxNow: `boxnow_cod`, the parcel id,
   `amount_to_be_collected`). Cash has no provider to refund it through, but
   it IS refundable: once collected it counts toward what `refund_order`
   can refund, and the merchant pays it back themselves and records how
   (§7.2, §7.3). Until the courier's payout is reconciled the ledger holds
   nothing for the order and there is nothing to refund.
   A payout row settles an order only when it is that order's own: the
   shipment is found by `POD`; `Customer_RefNo_1/2` (order pk / uuid) are a
   fallback ONLY when the voucher could not be read, never when it is readable
   but unknown (a parcel made by hand in the ACS portal), and a payout that
   remits a different sum than the shipment's COD amount is left unattributed
   for staff review (the "payouts that match no shipment" list) instead of
   being settled at the shipment's amount.
   The status flip and that row are ONE atomic step under the order lock
   (`OrderService.settle_cod_payment`, shared by the ACS reconcile, the
   staff "received outside ACS" action and BoxNow's delivered event): a
   failed ledger insert rolls the status back, so the next payout run still
   sees PENDING instead of skipping an order that is paid but unrecorded.
   The status is read under the lock, so two callers racing for one order
   settle it once, and `order_paid` plus the DELIVERED -> COMPLETED
   auto-advance run only for the call that did.
6. Customer email + WS toast at every meaningful transition.
```
