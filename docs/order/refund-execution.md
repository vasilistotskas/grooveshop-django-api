# Order system: how a refund is carried out

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

This is the second half of §7.2. The first half, what a refund returns, is
in [`refunds.md`](refunds.md).

**What `refund_order` refunds comes from the ledger, not from the order.**
`Order.amount_due` is the amount owed when the order was created and is never
consulted. Under the order row lock it reads what is left (captured minus
refunded, payment by payment) and:

- refuses an amount that is not positive, and an amount above the remainder
  (`Refund amount (X) cannot exceed the refundable amount (Y)`), before any
  provider is called; a repeat refund that finds nothing left raises
  `This order has already been refunded.`; an order the ledger holds no
  provider payment for raises
  `This order was not paid through a payment provider...` (cash on delivery, a
  gift card, a hand edit);
- plans the refund payment by payment, oldest first: an order paid twice holds
  two CHARGE rows and a refund larger than one is split across them, one
  provider call each, one REFUND row each (a COD_COLLECTION share is recorded
  with no provider call, see above);
- works in **three steps, so no transaction is open while a provider answers**.
  Every connection kills a session idle in a transaction after ten seconds
  (`idle_in_transaction_session_timeout`, `src/settings/database.py`), and
  `refund_order` used to hold its transaction and the order lock across the
  provider's HTTP call: a slow provider meant a refund made there and lost here
  (and, inside `cancel_order`, a whole cancel rolled back). Now: **(1) claim**:
  under the order lock, validate against the ledger and write one
  `RefundAttempt` per share (`src/order/models/refund_attempt.py`: order,
  `kind` `PROVIDER` or `STORED_VALUE`, the charge it refunds (none for stored
  value), amount, reason, a UUID `idempotency_key`, `PENDING` / `SETTLED` /
  `DECLINED`), then commit. While an attempt is `PENDING`, whatever its age,
  its amount is held back from its charge in `PaymentLedger.balances` and its
  VAT-rate allocation keeps its room: nothing frees it on a timer, only a
  settled or declined attempt lets go (`RefundAttempt.objects.live()`). **(2)
  call** the provider with
  `idempotency_key=f"refund-{attempt.idempotency_key}"` and no transaction
  open. **(3) record**: under the lock again, `record_refund` writes the REFUND
  row, settles the attempt and signals `order_refunded`;
- records each refund the provider accepted as it goes, and signals it, so a
  failure on the second payment keeps (and has announced) the first; the shares
  after a refused one are released (`DECLINED`), they were never called;
- returns a `RefundResult` (`src/order/services.py`): `refunded` (the money
  that actually moved, a `Money`), `refund_ids`, and `declined` (the provider's
  answer for the share it did not make, `None` when every share was). Never a
  yes/no: a refund declined part-way reports what DID move, so no caller tells
  staff "nothing was refunded" about money that left (`complete` and
  `paid_something` are the two questions callers ask). `declined["error"]` can
  carry raw provider text, so it is for logs and staff screens, never a
  customer;
- for an order whose capture the ledger never recorded (it settled while the
  previous release was serving, or was created from an already-succeeded intent
  whose event never reached it), reads the capture from the provider once and
  records it (`PaymentLedger.ensure_charge`); a capture the provider cannot
  confirm refuses the refund (staff NOTE, nothing sent) instead of leaving
  money returned and the order unbooked.

**Attempts, keys and what a retry means** (Stripe:
docs.stripe.com/api/idempotent_requests). The attempt's key is what the
provider receives. Stripe keeps the first answer for a key for at least 24
hours and answers a repeat of the same request with it instead of refunding
twice; `StripePaymentProvider.idempotent_refunds` says so. A request that
matches a live pending attempt (the same amount, or a refund of everything once
the attempts hold everything that was left: a double click, a retry after a
crash or a timeout) reuses that attempt and its key, so Stripe hands back the
refund it made and the retry records it once. Viva's cancel call takes no key
(`idempotent_refunds` is False), so a provider like it is NEVER asked twice for
one refund: an answer that was lost cannot be told from a refund never made,
and the request is refused with "its result is not known yet"; Viva's 1797
webhook books the refund and settles the attempt. Cash shares are not attempts
to resume: their `repayment_reference` is their key. A different amount is its
own refund under its own key. **What each outcome does to the attempt**: a
refusal (`success` false without `outcome_unknown`: Stripe 4xx,
`Success: false` from Viva) marks it `DECLINED` and frees its amount; an
**unknown outcome** (`outcome_unknown` in the answer: a timeout, a dropped
connection, a 5xx, or Viva accepting without a `TransactionId`, which cannot be
booked under an id the 1797 would not repeat) keeps it `PENDING`, because the
refund may have been made, writes a staff note saying so, and lets the webhook,
a retry or the reconciliation below settle it. It keeps holding its amount
until one does. **A webhook that books the refund mid-call** (`refund.created`
or 1797 arriving before step 3) settles the matching `PENDING` attempt (same
charge, same amount, whatever its age) in `record_refund`, and step 3 then
books nothing and signals nothing: the refund is counted once and announced
once. A Stripe attempt Stripe ANSWERED with an error keeps that answer for its
key for up to 24 hours: refund it in the dashboard instead, and the webhook
records it.

**Resolving an attempt whose outcome stays unknown**
(`src/order/refund_reconciliation.py`). A provider attempt still `PENDING`
after `REFUND_RECONCILE_AFTER_MINUTES` (default 60, an env setting that must
stay under the 24 hours Stripe remembers a key) is *overdue*
(`RefundAttempt.objects.overdue()`, `RefundAttempt.is_overdue`). An overdue
attempt is never sent again: a retry of its amount is refused (Stripe may have
forgotten the key, so a resend could refund twice). Three things resolve it,
all through the ledger paths a refund made in one go takes
(`OrderService.book_share` settles, a `DECLINED` status frees):

- the provider's webhook (`record_refund`);
- the beat task `order.tasks.reconcile_pending_refunds`
  (`reconcile-pending-refunds`, hourly, through
  `tenant.tasks.fanout_reconcile_pending_refunds`), which asks the provider
  through `PaymentProvider.find_refunds` and settles or declines only on an
  unambiguous answer. **Stripe**: `refund_payment` puts the attempt's key in
  the refund's `metadata` (`refund_attempt`); the lookup lists refunds by
  `payment_intent` (that call cannot filter by metadata) and keeps the one
  carrying the key. `succeeded` and `pending` settle, `failed` and `canceled`
  decline, `requires_action` decides nothing. **Viva**: the cancel call carries
  no reference, so the lookup lists `GET /api/transactions/?ordercode=` (Basic
  auth) for each of the order's codes and keeps children of the payment
  (`ParentId`) of a refund or void type for exactly the attempt's amount (this
  listing nests the type, `TransactionType.TransactionTypeId`, where Retrieve
  Transaction has a flat `transactionTypeId`; checked against a real demo
  refund on staging, 2026-11-08); `StatusId` `F` settles, `E` declines, a
  refund the ledger already holds is not given to a second attempt. Nothing
  found, several candidates, a different amount, an unreadable provider, a cash
  share (no provider) or a missing credential leave it `PENDING` and stamp
  `RefundAttempt.looked_up_at` (so does a lookup that raises): it is never
  guessed, and from then on it is staff's
  (`RefundAttempt.objects.awaiting_staff()`, `RefundAttempt.is_awaiting_staff`;
  an attempt overdue for a second `REFUND_RECONCILE_AFTER_MINUTES` with no
  stamp is staff's too, so a lookup switched off or failing before it can stamp
  never hides one);
- staff, with the order's **Record refund outcome** action
  (`record_refund_outcome`, offered only where the lookup could not decide an
  overdue attempt, `awaiting_staff()`, and refused before: until the lookup has
  asked, the provider may still answer): they say the refund was made (with the
  provider's reference, or for cash paid back by the merchant how it was paid)
  or was not. A provider reference is never booked as typed: the provider is
  asked (`PaymentProvider.confirm_refund`: Stripe retrieves the refund and
  checks its PaymentIntent, amount and status; Viva finds the transaction among
  the refunds of the payment, checks ParentId, amount and status `F`), the
  refund is booked under the id the provider spells, and one the provider
  cannot confirm, or reports failed, is refused. The "already recorded" check
  ignores case (Viva's ids are GUIDs). It settles through `book_share` or
  declines, and writes an `OrderHistory` note with the user.

An attempt the lookup could not decide (`awaiting_staff()`) is listed on the
store dashboard (StoreAlerts, "Refunds with an unknown outcome") until it is
resolved. A stored-value attempt is never overdue: no provider is involved and
a retry of the refund finishes it.

**Stripe refund statuses** (docs.stripe.com/api/refunds/object): `succeeded`
and `pending` are refunds Stripe took on (a part refund reports `succeeded`
like a full one, so the status never says whether the order is fully refunded;
the ledger's sums do), and are recorded, in REFUNDED or PARTIALLY_REFUNDED,
both settled statuses a late `payment_intent.succeeded` cannot write over.
`requires_action` waits on the customer and `failed` / `canceled` returned
nothing: none is recorded, the order stays COMPLETED, and staff find Stripe's
refund id and status in a NOTE on the order. The provider no longer reports a
payment status at all (`refund_payment` returns `refund_id`, `amount`,
`payment_id`). A refund Stripe accepted and later fails (`refund.failed`,
`refund.updated`, `charge.refund.updated`) is reversed on the ledger:
`handle_stripe_refund_failed` writes one `REFUND_REVERSAL` row keyed on the
refund id, so the money counts as held and refundable again, `payment_status`
is derived again and staff are told. What the refund had released is not
taken back. [§4.2.0](payments.md) has the whole rule.

**The `order_refunded` contract** (`src/order/signals/__init__.py`). It carries
`amount`, a `Money`: what THIS refund paid back, and `refund`, the REFUND
`PaymentTransaction` row it recorded (the confirmation email is queued for it),
both never absent (receivers that use them declare them as required arguments,
so a sender that leaves one out raises). `order.payment_status` is already
written (by `record_refund`) and says how much of the order is back:
PARTIALLY_REFUNDED while part of the payment remains refundable, REFUNDED once
nothing does. **Whatever hangs off the order's total is released only on
REFUNDED, once, with the last refund**: the loyalty points
(`loyalty.signals.handle_order_refunded_loyalty`, which is all-or-nothing and
once per order). The gift-card redemption has no `order_refunded` receiver any
more (`giftcard.signals.handle_order_refunded_giftcard` is gone): `refund_order`
credits the cards itself, with the value of a refund beyond the provider's
money (`GiftCardService.credit_refund`, [§7.2](refunds.md)), and a cancel
returns the whole redemption (`giftcard.signals.handle_order_canceled_giftcard`
→ `giftcard.tasks.credit_refund_to_gift_cards`). A refund is paid back by the
provider first, and releasing the whole redemption on top handed back more
than the refund was worth (a EUR 5 refund once put EUR 60 back on a gift
card). The cards now get the value in parts, as each refund reaches past the
provider's money: a card can be credited more than once for one order, never
for more than it is still owed, which `credit_refund` reads under the card's
row lock.
The email, the toast, the history row and the Meta `Refund` event fire on every
refund, partial Stripe dashboard refunds included (they used to be silent).

**A refund the provider did not make is not recorded.** `refund_order` returns
a `RefundResult` with `declined` set and changes nothing for that share:
`payment_status` stays COMPLETED (or whatever the shares already made left it),
no REFUND row is written for it, `order_refunded` does not fire for it. It
leaves a staff-only `OrderHistory` NOTE
(`Refund via <provider> was not processed: …`; `NOTE` is not a customer-visible
history type). `cancel_order` still cancels and reports
`refund_info = {"refunded": False, …}` with no provider text, whose `message`
says "only part of the payment was refunded" when a split refund was declined
part-way; the admin "Cancel selected orders" action raises an error message for
it.

**Viva (`VivaWalletPaymentProvider.refund_payment`).** Cancel Transaction
requires `amount` (cents), so the request always carries it, and so does every
call now: the service always names an amount (a full refund is the ledger's
remainder, never "ask Viva what it captured"). HTTP 2xx is not success: Viva
reports refusals in a 200 body, so only `Success` true with `ErrorCode` 0
counts, and the body's `TransactionId` (the refund transaction) is the ledger's
reference for the refund. `DELETE /api/transactions/{id}` has no idempotency
parameter (Viva's `idempotencyKey` is on the `/acquiring/v1/transactions/{id}`
route), so none is sent; Viva refuses a refund above what is left on the
payment, and the ledger's unique constraint on the refund transaction id keeps
it from being recorded twice. Before #134 the amount was omitted and the body
ignored, so every in-app Viva refund booked REFUNDED while Viva kept the
payment at `F`.
