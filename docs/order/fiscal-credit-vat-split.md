# Order system: the VAT split of a credit document

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

This is part of §7.4, [`fiscal-mydata.md`](fiscal-mydata.md), between its
"Credit documents" and "CancelInvoice" paragraphs.

**VAT split: fixed when the money is claimed (`src/order/credit_allocation.py`,
`CreditAllocation`).** The receipt froze its gross and net per rate
(`Invoice.vat_breakdown`, shipping and fees included). Where a credit sits on
those rates is NOT worked out when the credit is issued: every source of a
credit stores its per-rate allocation when its money is claimed, booked or
decided, under the order lock, and `create_pending_credits` issues the credit
from that allocation only (`credit_breakdown(..., allocation)`: exactly the
allocation's rates, never spilling a capped rate onto another, a credit for
less than its allocation, as a stored-value share that returned less, takes it
in proportion). So the order credits are issued in cannot change a credit, and
a refund claimed first keeps its room however the later ones settle. The
sources: a **refund attempt** (a share of `refund_order`, allocated in
`_claim_refund`: a bare amount pro rata over the headroom left at that moment,
a refund by lines its own pieces at their own rates ("Refund by lines" in [§7.2](refunds.md)),
laid over its shares in order, and the claim is REFUSED before any provider is
called when the headroom cannot hold it); a **booked refund row no attempt
claimed** (a provider's webhook or dashboard refund,
`PaymentLedger.record_refund`: allocated when booked, pro rata over the
headroom then, capped, the one rule for money nobody claimed); a
**goods-returned chargeback** (`decide_chargebacks`: allocated at the staff
decision, capped to the headroom, so the credit is for what the receipt can
hold).

**The headroom is a plain sum** (`credit_allocation.headroom`): the receipt's
gross per rate (the order's VAT lines while there is no receipt), less the
allocations of every source that was not declined and whose money moves at or
after the receipt row (`moved_at`; an in-flight claim counts), less the credit
documents themselves (a source that has a credit is counted by the document's
own `vat_breakdown`, never by its allocation); money that moved before the
receipt is not subtracted because the receipt is issued net of it, at the
allocation's own rates (`invoicing._net_of`).

**Points owe no credit:** loyalty points are a discount (they lower the lines'
gross, so the receipt does not hold their value), so a refund's points share is
left out of the claim's allocation and the headroom check
(`PaymentLedger.split_stored_value` divides a stored-value share into gift card
and points the same way at claim and at execution) and
`refunded_before_receipt` counts only the gift-card value; a gift card is
payment the receipt holds and stays in.

**Decided, undecided, and who decides.**
`credit_allocation.allocate_order(order)` decides every source of the order
that has no allocation, oldest first, each over what the ones before it left,
reading the sources INSIDE the order's row lock. A source is **decided** when
it has rows: a row of amount zero says it owes nothing (the duplicate-payment
excess, a share reversed in full, an order with nothing taxable, or no room
left because SETTLED sources and issued credits took it), rows with amounts say
what it owes. A source with NO row is **undecided**: it owes nothing yet and is
never credited.

**One rule for both things a PENDING claim holds back, the room and the
excess:** if releasing every PENDING claim (without a refund) would give the
source more room, or more excess, than it gets now, it stays undecided
(`allocate_source` returns `False`); what settled sources and issued credits
took is final, cut to what fits, kept as `CreditAllocation.uncovered` on the
source's first row and noted once on the order. `allocate_order` stops at the
first undecided source, so decisions follow the order of the money and a
younger source never takes room an older one waits for (the younger one waits
too).

**Who calls it.** `generate_invoice` (new receipts, before the VAT breakdown),
`create_pending_credits`, a NEW refund claim (`_claim_refund`),
`PaymentLedger.record_refund` (a refund nobody claimed is decided there, over
the headroom of that moment) and `decide_chargebacks` (once, after it changed
its decisions; `credit_allocation.drop(decision__in=...)` clears the changed
ones first). A read path (a specimen render, the admin) never calls it. The
periodic task `allocate_credit_sources` (every five minutes, through
`tenant.tasks.fanout_allocate_credit_sources`, suspended stores included;
`src/order/credit_allocation_backfill.py`) decides what no write path has
touched since, so a source that waited for a claim is decided within five
minutes of the claim resolving, whichever path resolved it (reconciliation, the
admin outcome action, the provider's webhook, a decline); when it decided
sources of an order that has a receipt it requests the credit documents. There
is no data migration: the 0102 table is created empty and migrations stay
additive. A gift card return booked without an allocation (the previous release
serving after the migration) is found by `owing_sources` like any other source,
valued from the gift-card ledger and moved when it put value back; one that
moved before an existing receipt is left alone. `RefundAttempt` resolution
books a refund only for an attempt still PENDING (`OrderService.book_share`
re-reads it under the order lock): a refund the provider made for an attempt
staff declined meanwhile is booked as a refund nobody claimed, never reviving
the attempt. `receipt_deferred` means "waiting for undecided sources" and
nothing more: `allocate_credit_sources` clears it when it asks for the receipt
again, so a receipt that then fails follows the normal failure path.

**Whatever is undecided blocks the three things that depend on the books being
complete.** (1) The **receipt**: `_persist_invoice_row` raises
`ReceiptDeferred` (a `ReceiptNotIssuable`) while an undecided source remains,
because the receipt could not net it and, once the row exists, nothing would
ever credit it; `generate_order_invoice` flags the order
(`metadata["receipt_deferred"]`) and the periodic task issues the receipt once
nothing is undecided, then clears the flag. The receipt is netted by the
allocations that moved before it, and `refunded_before_receipt` (the
fully-refunded guard) adds up the same rows, so a duplicate payment given back
is not a return of the sale. (2) The **settlement credit** of a closed order
waits. (3) A **new refund claim** is refused (`PaymentError`, nothing sent to a
provider) until the older source is decided, and also while a claim still
PENDING holds part of the duplicate payment the new refund would be decided
against (`PaymentLedger.refundable_excess` with the claims held exceeds the
same with them released, counting only what the new refund itself would take,
`min(excess, amount)`: a claim holding excess the refund would not take anyway
never makes it wait; the same comparison, per source, decides whether
`allocate_source` leaves a source undecided); resuming an existing claim is not
refused. The refusal names only the claims that block
(`credit_allocation.blocking_claims`, `excess_holders`: those whose release
alone would change the outcome), never every live claim.

**Before any receipt** a source whose money moved is netted from the
receipt-to-be, capped by the order's own VAT lines (the sale); goods claimed
beyond what the order sold are `uncovered` whether or not a receipt is ever
issued, so an impossible "goods returned" decision (a dispute of a duplicate
payment decided returned after the sale was refunded in full) is flagged for
staff and the accountant.

**Money past the receipt** (`CreditAllocation.uncovered`, kept on the source's
first row and recomputed when a reversal shrinks what stands: at most what
stands less what the rows hold) has no credit document: the order's history
says so and the store dashboard lists the newest orders
(`credit_allocation.uncovered_orders`, "Refunded money beyond the receipt",
linking to the order list filtered by `books=beyond_receipt`). Staff clear an
entry with the order action "Acknowledge receipt differences"
(`acknowledge_uncovered`: sets `uncovered_acknowledged_at`, or
`understated_acknowledged_at` for a receipt short of goods, each kind on its
own timestamp so one never re-opens the other; writes who to the order's
history).

**A deferral is visible and bounded by a person.** A receipt is due when the
goods are dispatched, so the dashboard lists every order whose receipt is
deferred ("Receipts waiting for a refund", naming the claim it waits for,
linking to `books=receipt_waiting`), and the order action "Issue receipt" says
it is deferred and why instead of "Scheduled". A gift card and points return a
crash left PENDING is never asked of a provider and has no outcome to record:
repeating the refund of the share's own amount resumes and finishes it
(`_attempts_to_resume`): the refund dialog offers that amount, and the action
stays offered while such a return is PENDING even when it holds everything that
was left (`PaymentLedger.refund_open`). Once it is older than the reconcile
window it is in the alert "Gift card and points refunds left unfinished"
(`RefundAttempt.objects.stalled()`). A flag that no longer applies (the order
was refunded in full, myDATA was switched off) is cleared the first time
`generate_order_invoice` finds the receipt not issuable, so it is not asked for
again. Clearing it when myDATA is off is deliberate: the flag means "waiting
for a refund claim", not "waiting for a receipt", and an order shipped while
the store issues nothing is in the same state as every other such order (no
receipt is issued automatically when the store later becomes ready; staff use
"Issue receipt").

**A duplicate payment owes no credit.** A refund pays the excess the ledger
holds (`PaymentLedger.excess_held`: `held` above what the order is STILL for,
by more than the rounding tolerance; what it is still for is
`calculate_order_total_amount()` less the goods already returned, which is, for
each provider source that moved before that moment, the goods it returned
before any reversal (`CreditAllocation.goods_claimed`: what it owed after the
duplicate payment, kept on the source's first row and never scaled) less what
the provider reported failed of it BY THAT CUT
(`credit_allocation.goods_returned`; a reversal booked at or after the cut is
not known there, so a source decided late owes what it would have owed decided
on time). The excess is also capped by money: at most what the provider still
holds (captures less refunds less every dispute, `PaymentLedger.excess_held`),
so no refund can pay back more than is left.

**One cut for every input:** a source owes the same whenever it is decided,
because everything its decision reads is as it stood at its own cut (the ledger
up to its row, the goods and reversals before it, the claims made before it),
or the source waits while an input that could still change is unresolved.

**A chargeback never moves the excess:** what a lost dispute took is sale money
lost, never a credit and never what a duplicate payment is measured against, so
`held` does not take it off and `goods_returned` ignores decisions. A
goods-kept dispute never reduces what the order is still for and never eats the
duplicate; a goods-returned one takes as much from what is held as from what
the order is for, so it nets to zero, and is a credit source at its decision.
The operational overcharge flag is a different figure
(`PaymentLedger.overcharge_held`: what the customer paid above what the order
is for, in money, counting what a dispute took back and the goods of a returned
one), so a dispute on the duplicate payment itself clears the flag while the
refund of that payment is still judged against the excess. A duplicate payment
stays a duplicate whether the dispute is undecided, decided kept (goods kept
means the receipt stands: a chargeback never reduces what the order is still
for and never eats the duplicate) or decided returned (its credit comes from
its own allocation, at the decision), so the excess never depends on when staff
decide and nothing waits for them; `sync_overcharge` stamps
`Order.overcharged_at` from `overcharge_held`, written once a refund's
allocation is decided) back first: that part gives a duplicate payment back, it
does not reverse the sale, so it takes no room on the receipt and gets no
credit document (`PaymentLedger.refundable_excess`, read as it stood at the
source's own moment: the ledger rows before that row, ties by id, which already
hold what every earlier refund took of the excess, less the provider claims
made before it, each by how it stands and judged by when it was MADE, never by
when its refund row was written: PENDING holds its DUPLICATE PART (released
only in the undecided check), SETTLED holds its duplicate part in both modes
(counted here when its refund row sorts after the source's row, because the
ledger up to the source does not hold it yet), DECLINED holds nothing; a
claim's duplicate part is its amount less the goods it claimed
(`CreditAllocation.goods_claimed`, fixed when it was made), because a claim for
goods takes money out of the ledger and adds the same to the goods returned: it
leaves the excess where it was, so it counts on BOTH sides of the excess or on
neither, never netted from what the ledger holds alone; a later payment or
refund never changes it, so the same refund gets the same excess whenever it is
allocated). Only the remainder goes through the headroom check, so a duplicate
stays refundable after the sale took the receipt's room.

**A reversal comes off the refund's duplicate part first, then its uncovered
part, and only then returns room** (`scale`: the goods that stand are at most
the refund less what was reversed; a refund decided after it was reversed owes
the same, `allocate_source`). A goods-returned chargeback is a credit source at
its DECISION (its allocation moment), never at the dispute.

**Credits are issued net of reversals.** A refund the provider reports failed
scales its allocation down by everything reversed (`free_reversed`) and is
credited for what stands (`refund.amount` less `reversed_of(refund)`); a refund
reversed in full gets no credit and a zero row. A credit already issued stands
as the record of what was sent to AADE: the order gets a history note and the
store dashboard an alert (`credit_allocation.reversed_credits`) until an
accountant cancels the document (its allocation is scaled all the same, because
the goods that came back are what the provider really paid). A refund the
RECEIPT was issued net of (its money moved before the receipt) and that the
provider then reports failed has no credit to cancel: the receipt is short of
goods the customer kept, so `free_reversed` records the freed amount on the
source (`CreditAllocation.understated`), writes an order-history note and the
dashboard lists it ("Receipts short of goods the customer kept",
`understated_orders`); the accountant corrects the receipt and staff clear it
with the same "Acknowledge receipt differences" action.

**When is a claim judged?** A claimed refund is decided at the moment it was
MADE: a claim made before a duplicate payment existed owes goods credit
whatever arrives while it is in flight; a refund nobody claimed is decided at
its own ledger row. The randomized invariant test
`tests/integration/order/test_credit_invariants.py` replays random sequences of
every event (payments, receipts, claims that succeed, time out or lose their
answer, settlements by reconciliation, webhook or staff, declines, dashboard
refunds, reversals, chargebacks and their decisions, gift card and points
returns) against an oracle that applies this rule independently, and checks
that credits never exceed the receipt per rate, that credited + uncovered
equals the goods returned, that a receipt is net of exactly the goods part of
earlier refunds, that nothing is booked or credited twice and that nothing is
left deferred or undecided. `CREDIT_INVARIANT_SEEDS=0-999` runs more seeds
locally.

**A lost dispute is credited only through staff's decision on its goods**
(`decide_chargebacks`), never by the settlement of a REFUNDED order: the
settlement waits while any chargeback of the order has no decision (the order
is on the dashboard's chargebacks-to-settle worklist, and a decision requests
the credit documents again), and what a chargeback took whose goods the
customer kept is left out of it, so "goods kept" means no credit for that part,
ever. A booked refund that a `RefundAttempt` later claims
(`PaymentLedger.settle_attempt`) drops its own allocation and the attempt's is
scaled to what really moved (`credit_allocation.link_attempt`). The settlement
of a closed order is the one credit without an allocation: it takes what the
receipt still holds at every rate.

**Our cap is the only over-credit guard.** Because AADE cannot link an 11.4 to
its receipt it cannot check a credit against the receipt's total (error 319
belongs to linked credits), so `credit_breakdown` is the sole protection at
issue: a credit's NET is derived cumulatively, from what the rate's total
credited gross splits to (`order.taxation.split_gross`), capped at the
receipt's net for the rate, less the net already credited. Any number of
partial credits stay under the receipt and the last lands exactly on it. A
credit the receipt cannot hold is not sent: a staff `OrderHistory` note says
why.
