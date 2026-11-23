# Order system: Viva Wallet payments

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here. Stripe, cash on delivery and
what every provider shares (per-tenant credentials, disputes) are in
[`payments.md`](payments.md).

### 4.3 Viva Wallet online

Settlement: `src/order/payments/viva_settlement.py::handle_payment_created`,
run by the webhook (`src/order/views/viva_webhook.py`) and by
`viva_settlement.settle_landed_payment`, which reads back a payment whose
webhook has not landed (see **A lost webhook** below).

Differences from the Stripe path that are easy to miss:

- **Row lock** is acquired in `viva_settlement.apply_event`, not inside
  a service method. The whole event runs inside one
  ``select_for_update`` block, and no Viva call is made inside it: each
  event's ``EventRead`` (`_EVENT_HANDLERS` pairs each handler with one)
  retrieves what the handler needs before the lock (the transaction; for
  a 1797 also the payment its ``ParentId`` names when the ledger lacks
  it, read only once the refund is on one of the order's codes, and sent
  as one quoted path segment), and the handler judges that answer under
  the lock.
- **Idempotency** uses the ``VivaWebhookEvent`` table
  (``src/order/models/viva_webhook_event.py``), unique on
  ``(transaction_id, event_type_id)`` and written last in the same
  transaction — not a metadata flag like Stripe's
  ``webhook_processed_{event_id}``.
- **Inline status mutation**: ``order.status = OrderStatus.PROCESSING``
  + ``order.save(update_fields=[...])``, NOT
  ``OrderLifecycleService.update_order_status(...)``. The post-save signal
  still fires, but the canonical state-machine validator is
  skipped — Viva trusts that ``status==PENDING`` before the
  payment landed.
- **PR #7 PROCESSING suppression** is wired (mirrors Stripe): the
  handler calls ``OrderLifecycleService.suppress_customer_status_notifications(
  order, "PROCESSING")`` immediately before the inline status flip
  so the customer doesn't get back-to-back payment-confirmed +
  order_processing emails when Viva confirms payment. Without
  this, the post-save handler would dispatch both.
- **Confirmation** is the same as Stripe's: the handler confirms the
  order (``OrderLifecycleService.confirm``, ``Order.confirmed_at`` claimed
  once), and ``order_confirmed`` queues the payment-confirmed email, the
  staff "New order" email and the payment-confirmed toast. A 1798 records
  the failure and tells no one.
- **The charge is recorded on the payment ledger** ([§7.3](payment-ledger.md))
  once the
  transaction is verified (it belongs to the order, the currency and
  amount match, status `F`): the verified amount, keyed on the
  transaction id, before the canceled-order and settled guards. A second
  payment under another of the order's checkout codes is a second CHARGE
  row (`Order.payment_id` still names only the latest).

**A lost webhook.** Viva's webhook is the only thing that marks an order paid,
and it can be lost (an outage, an endpoint that cannot be reached). Two places
read Viva instead: `auto_cancel_stuck_pending_orders` before it releases an
unpaid order ([cancel.md](cancel.md), the sweep), and `viva_return` when the
buyer comes back from the hosted page. `VivaWalletPaymentProvider.landed_charges`
lists each payment order's transactions (`GET /api/transactions/?ordercode=`,
Basic auth) and keeps the payments that took money (`StatusId` in
`VIVA_MONEY_TAKEN_STATUS_IDS`: `F`, `R` refunded since, or a dispute status; a
positive amount; no `ParentId`, which a refund carries). A finished one is
applied through
`viva_settlement.apply_event` as the 1796 its webhook carries, so it is
verified with Retrieve Transaction, booked, settled and confirmed exactly as
the webhook would, and the `VivaWebhookEvent` row makes the webhook a no-op
when it does land. Money Viva took that settles nothing (one refunded or
disputed since, which no webhook applies, or one the checks refuse without
a flag) never lets the order be canceled as unpaid: the order is flagged for
the store's review (`viva_settlement._flag_for_review`, under the order's
lock), so it is on the "Payment review" worklist, which the sweep leaves
alone. A Viva that cannot be read raises
`PaymentStatusUnavailableError`: the sweep keeps the order, `viva_return`
answers with the order as it is. An answer with no `Transactions` list, or a
finished payment without a finite amount, is one that cannot be read; only an
explicit empty list says nobody paid. Every Viva amount is parsed by
`payment.provider_amount`, which refuses an absent value, `NaN` and
`Infinity` (JSON tokens `requests` parses).

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
   from our own rows, or from Viva. A `ParentId` the ledger does not hold is
   read from Viva once and recorded as a CHARGE when it is the order's own
   `payment_id` (its capture was never recorded) or a captured transaction on
   one of the order's codes (a payment whose 1796 is late, lost, or verified
   and waiting for the order's lock); unreadable → 500, Viva retries. The
   refund is then booked against it, so the payment's own 1796 that follows
   finds the order refunded and neither settles nor ships it. Anything else
   is skipped without a record (see below), as is a "parent" that is no
   capture (a refund, a negative amount). The 1796 that follows books its charge under the ledger's
   spelling of the id, so a `ParentId` in other letters is one CHARGE. A 2054
   passes the sale it has already read, so the sale is retrieved once.
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
   `Order.status` is not touched, with one exception for an order still
   `PENDING` (never confirmed), which no sweep selects once its payment is
   settled. A refund that leaves it holding none of the customer's money
   (`PaymentLedger.held` at zero; a gift card or points it also paid with
   go back with the cancel) cancels it (`cancel_order(refund_payment=False)`,
   stock released). One that leaves part held flags it for the store
   (`OrderService.flag_payment_mismatch`: what is held against what the
   order is for), so it is on the "Payment review" worklist
   (`payment_hold.payment_review_orders`). An order already waiting for
   that review is the store's to close: the refund is booked and nothing
   is canceled (a charge for the wrong amount is never booked, so the
   ledger cannot say what is still at Viva).

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
payments as "fee not reported". A reversal whose parent
cannot be read or is none of the order's payments (a `ParentId` that is no Viva
transaction id, one Viva answers 404 for, one on another order, a refund) is
answered 200 and recorded nowhere
(`viva_settlement.OUTCOME_NOT_RECORDED`), so it cannot stand in for Viva's own
event for that refund.

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
