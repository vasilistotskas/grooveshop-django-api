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
  no platform-account concept or fallback of any kind. Webhooks are
  HOST-ROUTED per tenant:
  `https://api.<tenant-domain>/stripe/webhook/<uuid>/` — django-tenants
  selects the schema, then dj-stripe's UUID view verifies against the
  per-schema `WebhookEndpoint` row secret. Provisioning (APIKey row +
  Stripe endpoint incl. secret): `manage.py bootstrap_stripe
  --schema=<schema>` (idempotent; `--rotate-endpoint` to re-mint). Every
  tenant has its own `stripe_secret_key` + `stripe_publishable_key` on the
  Tenant row before `bootstrap_stripe` runs.
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

**One payable link per order.** Asking for a payment link
(`OrderViewSet.create_checkout_session`, every caller: the storefront's
payment view and "pay now", the agent gateway) is idempotent. The provider
is asked first, with no row locked (`PaymentProvider.live_checkout_session`):

- a link that can still be paid is returned again with its own expiry, and
  nothing is minted (`successUrl`/`cancelUrl` are the ones it was opened
  with);
- a link that was paid while the order is still unpaid (its webhook has not
  landed) answers **409 `payment_in_progress`** with `Retry-After: 10`; no
  link is minted, so the buyer cannot pay twice;
- only when none can be paid is a new one minted, and only then does the
  provider's minimum session lifetime apply (`payment_window_expired` when
  less of the window is left); a refused request closes nothing;
- a provider that cannot say answers **503 `payment_provider_unavailable`**
  with `Retry-After: 30`: a link is never minted blind.

Viva is read with Retrieve order (`GET /api/orders/{orderCode}`, Basic auth)
for every code in `metadata.viva_order_codes`: `StateId` 0 Pending is
returned, 3 Paid is in progress, 1 Expired and 2 Canceled are gone; an
answer with no known `StateId` is "unreadable". Viva is offered only to a
store holding all four credentials (`is_configured_for_tenant`): the OAuth
pair mints payment orders and reads transactions, the Basic pair (Merchant
ID, API Key) reads payment orders and refunds. Each call is gated by what it
needs: the provider builds with the OAuth pair, so a store holding only it
still settles its webhooks. Every Basic-auth call checks
`has_basic_credentials` before sending and fails the way it fails when Viva
cannot be read: Retrieve order is unreadable (503, an order placed before the
store lost the pair), a refund is refused, a refund lookup has nothing to say.
None sends empty credentials. A Retrieve order answer that is not a
JSON object is "unreadable" too.
`ExpirationDate` carries no offset and is Greek local time
(`VivaWalletPaymentProvider.TIME_ZONE`, Europe/Athens); the hour repeated
when summer time ends is read as its later instant (`fold=1`), so a payable
code is never taken for expired early. Viva forgets a code
some time after it ends and answers 404 (a code two months old does); a 404 is "gone" only once the order's
hold and the grace window have ended (`payment_hold.hold_ended`), else it is
"unreadable". Stripe is read from the Checkout Session's `status`: `open` is
returned, `expired` is gone, and `complete` is in progress only while its
payment may land (`payment_status` paid, or its PaymentIntent processing,
requiring capture or action, or succeeded); a delayed method (SEPA Debit)
whose intent failed leaves the session `complete` for ever, and it is gone.
`checkout.session.expired` never writes over the recorded session: a late
event for an older session, or for one that lost a concurrent request, would
make the next request mint a second payable one. A hosted session and a PaymentIntent are never
payable together: an open intent the order holds (an agent's delegated card
awaiting its confirmation) is closed (`close_payment`) before a session
opens; one that may still be paid refuses the session.

One payable instrument per order is enforced where every newly opened
hosted session is written: `OpenPayment.lock_unchanged` (`src/order/payments/
payment.py`), under the order's row lock (`create_checkout_session`). Each
request reads `OpenPayment`
(status, payment status, `payment_id`, the recorded session) before its
provider call; when anything of it changed meanwhile, it records nothing.
Two concurrent link requests: the second records nothing of its own, leaves
the hold alone and answers with the first one's link. A link request that
loses to another change of the order's payment meanwhile (an agent's
delegated payment, a cancel, a webhook) answers 409
`order_changed_during_payment`, and its Stripe session is expired. Reusing a
live session closes an open intent first, as minting does. Its own
link reached nobody; a Stripe one is expired (`discard_checkout_session`),
a Viva one is left, as Viva's cancel is not documented well enough to build
on.

### 4.1 Stripe online (`payment_intent.succeeded`)

Webhook handler:
`src/order/signals/handlers.py::handle_stripe_payment_succeeded`.

```text
payment_intent.succeeded → handle_stripe_payment_succeeded
                 │
                 ├── webhook_processed_{event_id} idempotency guard
                 │
                 ├── OrderService.handle_payment_succeeded(payment_intent_id, captured=amount_received)
                 │   ├── lock Cart before Order (order.payments.cart_clear, matching checkout)
                 │   ├── select_for_update on Order row
                 │   ├── PaymentLedger.record_charge (CHARGE row, whatever state the order is in)
                 │   ├── mark_as_paid → payment_status=COMPLETED
                 │   ├── clear_locked_cart_for_order (inside transaction, on transition to paid)
                 │   ├── suppress_customer_status_notifications(PROCESSING)
                 │   ├── update_order_status(PROCESSING)
                 │   └── _dispatch_shipment_creation_task (on_commit)
                 │
                 │   └── returns PaymentEventOutcome(order, previous_payment_status, applied)
                 │
                 │   only when outcome.applied:
                 ├── OrderHistory PAYMENT row (previous = the real prior status)
                 └── OrderLifecycleService.confirm (claims confirmed_at once)
                       └── order_confirmed → confirmation email, staff email,
                           payment-confirmed note (on_commit, emails-and-notifications.md)
```

**A payment no order carries pages staff.** When
`handle_payment_succeeded` finds no order for the intent (by `payment_id`,
then by the order its server-set binding metadata names, `_bound_order_uuid`),
it books nothing and calls `OrderService.report_payment_without_order`.

- **Only the store's own payments alert.** An intent whose metadata does not
  carry our binding for this tenant (`payment_binding.own_purpose`: a Payment
  Link, a Stripe Invoice, Terminal, a charge made in the Stripe dashboard, or
  another store's) is logged at WARNING and nothing more.
- **The mail is a task, not a call in the webhook.** The service sends
  `payment_without_order_found` (the order services import no task), and its
  receiver queues `order.tasks.alert_payment_without_order_task` with
  `dispatch_on_commit`: a webhook that rolls back queues nothing, and the
  mail never runs inside dj-stripe's `transaction.atomic`. Stripe has its
  answer by then and redelivers nothing, so the task retries a transient mail
  error itself (`MAIL_TRANSIENT_ERRORS`). It re-checks that no order carries
  the intent, takes the claim on the intent id (`cache.add`,
  `PAYMENT_WITHOUT_ORDER_CLAIM_KEY`, a week), sends
  `ops.payment_without_order` (declared in `src/order/emails.py`, listed in
  [the email types](../emails/types.md)), and gives the claim back when the
  send fails, so the retry sends. Alerts once per intent however many copies
  run (a redelivery, the polled path, a retry).
- **Never for** a gift-card purchase's intent (the handler resolves those
  first and stops), nor one an order carries or is bound to (Checkout
  Session, agent).
- **At once.** Every intent of ours is opened for an order that already
  exists (`order`) or for a gift-card purchase (`giftcard`), so nothing is
  waited for: an intent bound to an order that does not exist alerts at
  once. An intent bound with any other purpose (an old `cart` binding) is not
  one of ours and is only logged.

**The collected amount must be the order's total.** `handle_payment_succeeded`
(and the `checkout.session.completed` handler) compare what Stripe collected
with `order.calculate_order_total_amount()` (currency, and amount within
`PAYMENT_AMOUNT_TOLERANCE`) before `mark_as_paid`. A charge that does not
match is booked on the ledger (the money moved) but does not pay the order:
`OrderService.flag_payment_mismatch` stamps `AMOUNT_MISMATCH_FLAG` (which
also keeps `auto_cancel_stuck_pending_orders` away from it), notes the order
and mails `ops.payment_mismatch`; the Viva amount/currency refusals
use the same function, and it flags a payment once: the same payment met
again while the order waits for that review (a read-back's next run, a
refund of it, a redelivery) changes nothing and emails no one. The flag is
never cleared, so while the order is
still undecided (PENDING, and the charge not accepted: payment not
`COMPLETED`, whatever else it says, a refund in full or in part included;
`payment_hold.payment_review_orders`) the order detail serves
`paymentAwaitsReview: true` and `paymentDecidedBy: null`: a client must not
offer a new payment link. The same set is the store's worklist: the admin's
"Payment review" filter (`payment_review=yes`) and the dashboard's "Payments
to review" count. Once the store accepts the charge or moves the order on
from PENDING (fulfils or cancels it), `paymentAwaitsReview` reads false. A null `paymentDecidedBy` with
`paymentAwaitsReview: false` is an order no sweep decides for another reason
(the sweep switched off, a payment the provider is processing, an order that
waits for no online payment).

**A poll never writes `COMPLETED` itself.** `GET /order/{id}/payment_status`
(`OrderService.get_payment_status`) does not write the provider's status onto
the row. A polled COMPLETED settles through the same
`handle_payment_succeeded` (and announces through
`announce_stripe_payment_applied`, shared with the webhook and reached by the
`order_stripe_payment_polled` signal), only for a Stripe
intent no gift-card purchase holds; any other provider's payment is settled by
its webhook alone; nothing else polls and writes a payment.

**A poll records only an open payment's state**
(`_POLLED_STATUS_RECORDED` in `src/order/services.py`): PENDING, PROCESSING
and FAILED as reported, and a provider's `canceled` (a Stripe intent closed by
a hosted link opened in its place, a Viva `X`) as **FAILED**. The
attempt is over, not the order: CANCELED is the payment state of a closed
order (`Order.settle_unpaid_payment`), and on an open one it would read as
settled, so neither bucket of `auto_cancel_stuck_pending_orders` would select
the order, its stock would stay reserved, and a later success would be
ignored as stale.
FAILED keeps the order payable (a new link, `create_checkout_session`) and is swept after
`ORDER_AUTO_CANCEL_FAILED_PAYMENT_MINUTES`. A polled REFUNDED or
PARTIALLY_REFUNDED is never written: the ledger derives those from the refunds
it booked ([§7.3](payment-ledger.md)).

**A provider that could not be asked is not a payment outcome.** Each
provider's `get_payment_status` raises `PaymentStatusUnavailableError` for a
network error, a timeout, a rate limit, an outage or an answer it could not
read, never FAILED, which the 30-minute sweep would act on. Nothing is
written. `GET /order/{id}/payment_status` answers 503
`payment_provider_unavailable`, `PaymentLedger.ensure_charge`
records no charge, and the Viva webhook's verification raises so Viva
redelivers.

`applied` is False for a stale event against a settled payment, a
repeat of one already applied, and a charge on a canceled order
(`record_payment_after_cancel` books it and alerts ops): none of them
gets a history row or a customer email. `handle_payment_failed` returns
the same outcome; an applied failure is recorded (`FAILED`, a history row)
and tells no one: a card order is announced only when it is confirmed.

The `suppress_customer_status_notifications(PROCESSING)` call is
load-bearing: without it the customer receives both an
`order_payment_confirmed` email AND an `order_processing` email within ms
([§6.3](emails-and-notifications.md)).

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

**The session's code is shared.** `checkout.session.completed` settles through
`src/order/payments/stripe_settlement.py::settle_paid_checkout_session`, the
same code `stripe_settlement.settle_landed_payment` runs for a session or a
succeeded intent read back from Stripe when the webhook never landed (the
sweep, [cancel.md](cancel.md)). A session event for an order this very
PaymentIntent already settled (a redelivery, or the webhook arriving after the
sweep settled it) changes nothing: no second history row, shipment request or
confirmation.

### 4.2a Stripe fee (`charge.succeeded`, `charge.updated`)

`src/order/payments/payment_fees.py`. A Stripe fee is the `fee` of the charge's balance
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
  `REFUND_REVERSAL` row (`RefundService.book_refund_reversal`, writing it
  through `PaymentLedger.record_refund_reversal`) keyed on the
  refund id: the three event types and redeliveries book it once. It is capped
  at what the ledger holds as refunded on that charge, and a refund the ledger
  never counted (nothing to reverse) is logged and ignored. The refund row it
  reverses is found by the same refund id (`handle_stripe_refund_made` books
  under it), so the credit it would have owed is freed
  (`credit_allocation.free_reversed`) and the overcharge flag re-derived, both
  in `book_refund_reversal`.
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
  favor" (docs.stripe.com/disputes/withdrawing). `RefundService.refund_order`
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
  decision; `LoyaltyService.reverse_order_points` skips the restore
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
email still go out, once (`order.payments.disputes.announce_chargeback`; Viva remembers
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

In [`payments-viva.md`](payments-viva.md): settlement, the webhook events,
refunds and disputes, the return page and a lost webhook.

### 4.4 COD / PAY_ON_DELIVERY (offline)

```text
1. create_order_from_cart → status=PENDING, payment_status=PENDING
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
