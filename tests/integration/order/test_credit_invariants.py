"""Randomized invariants of refund credit allocation (PR #530).

Every seed builds a receipt order and applies a handful of random events
through the REAL service and ledger paths, in any order:

  dup           a second payment (a duplicate the customer made)
  receipt       the order ships and its receipt is issued and submitted
  refund        ``OrderService.refund_order`` for part of what can be
                refunded; Stripe answers, times out (a claim left PENDING)
                or loses its answer (made, but nobody knows)
  refund_full   ``refund_order`` for everything
  settle_*      a PENDING claim settled by reconciliation (``book_share``),
                by the provider's webhook (``record_refund`` with no attempt)
                or by staff ("Record refund outcome")
  decline       a PENDING claim declined, by reconciliation or by staff
  dashboard     a refund nobody claimed (``record_refund``)
  reversal      the provider reports a booked refund failed
  chargeback    a lost dispute, and later the staff decision on its goods
  credits       ``create_pending_credits``

Then every claim still PENDING is resolved, the periodic allocation runs, the
receipt is issued if it never was, and the credits are created. An
independent oracle (below) replays the order's ledger in time order and says
how much of the money paid back was goods. The invariants:

1. credits never exceed the receipt, per VAT rate;
2. at rest, what the receipt does not hold of the sale, plus what was
   credited, plus what no credit could cover, is the goods returned;
3. a receipt is net of exactly the goods part of the refunds before it;
4. nothing is booked, credited or allocated twice;
5. no receipt is deferred and no source undecided at rest.

The oracle's one rule is the product rule of ``docs/order-system.md`` §7:
a refund pays a duplicate payment back first (the excess, which owes no
credit), and the excess is what the ledger holds above what the order is
STILL for, judged at the moment of the claim for a refund that was claimed
(a claim made before the duplicate existed owes goods credit, whatever
arrives while it is in flight) and at the refund's own row for one nobody
claimed.

``CREDIT_INVARIANT_SEEDS=0-999`` runs more seeds than the committed list;
a failing seed is shrunk to a minimal event sequence before it is reported.
"""

from __future__ import annotations

import itertools
import os
import random
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from unittest import mock

import pytest
import stripe

from giftcard.factories import GiftCardFactory
from giftcard.models import GiftCardTransaction, GiftCardTransactionKind
from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction
from order import credit_allocation, refund_reconciliation
from order.chargebacks import ChargebackDecisionError, decide_chargebacks
from order.credit_documents import create_pending_credits
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import PaymentStatus
from order.exceptions import PaymentError
from order.models import (
    ChargebackDecision,
    CreditAllocation,
    Invoice,
    Order,
    OrderItem,
    PaymentTransaction,
    RetailCredit,
)
from order.models.invoice import MyDataStatus
from order.models.refund_attempt import (
    RefundAttempt,
    RefundAttemptKind,
    RefundAttemptStatus,
)
from order.payment_ledger import PAYMENT_AMOUNT_TOLERANCE, PaymentLedger
from order.services import OrderService
from order.tasks import (
    allocate_credit_sources,
    generate_order_invoice,
    send_invoice_to_mydata,
)
from tests.integration.order.test_credit_allocation import (  # noqa: F401
    _render_and_site,
)
from tests.integration.order.test_mydata_lifecycle import (
    _eur,
    _mark_shipped,
    _order,
)
from tests.utils.mydata import FakeAade, mydata_enabled
from tests.utils.stripe_refunds import FakeStripeRefunds
from user.factories import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

ZERO = Decimal(0)
CENT = Decimal("0.01")
_ref = itertools.count(1)

#: The seeds CI runs (about two minutes: the suite keeps a file on one
#: worker). More run locally: see the module doc.
CI_SEEDS = "0-29"


def _seeds() -> range:
    spec = os.environ.get("CREDIT_INVARIANT_SEEDS", CI_SEEDS)
    first, _, last = spec.partition("-")
    return range(int(first), int(last or first) + 1)


def _money(x: Decimal):
    return _eur(str(x))


def _frac(total: Decimal, f: float) -> Decimal:
    if total <= 0:
        return ZERO
    amt = (total * Decimal(str(f))).quantize(CENT, rounding=ROUND_DOWN)
    return max(amt, CENT) if amt <= total else total


EVENTS = [
    "dup",
    "receipt",
    "refund",
    "refund",
    "refund_timeout",
    "refund_timeout",
    "refund_lost",
    "refund_full",
    "settle_rec",
    "settle_wh",
    "settle_staff",
    "decline",
    "decline_staff",
    "dashboard",
    "dashboard",
    "reversal",
    "chargeback",
    "decide",
    "credits",
]


def gen(seed: int):
    """The order and the events of one seed, all from its number."""
    rnd = random.Random(seed)
    lines = [("24", Decimal(rnd.randint(2000, 20000)) / 100)]
    if rnd.random() < 0.5:
        lines.append(("13", Decimal(rnd.randint(1000, 20000)) / 100))
    points = gift = ZERO
    if rnd.random() < 0.35:
        # Part of the first line paid with points (a discount, not
        # payment), part of the rest with a gift card (payment).
        points = Decimal(rnd.randint(100, int(lines[0][1] * 50))) / 100
        total = sum((g for _r, g in lines), ZERO) - points
        if rnd.random() < 0.6:
            gift = _frac(total / 2, rnd.random())
    seq = [
        (rnd.choice(EVENTS), round(rnd.random(), 4), round(rnd.random(), 4))
        for _ in range(rnd.randint(3, 9))
    ]
    final = [round(rnd.random(), 4) for _ in range(12)]
    return tuple((r, f"{g:.2f}") for r, g in lines), points, gift, seq, final


def _build_order(lines, points: Decimal, gift: Decimal) -> Order:
    """A receipt order of *lines* (rate, gross) with *points* of its first
    line paid by loyalty points and *gift* by a gift card; the card paid the
    rest and is the ledger's one charge."""
    user = UserAccountFactory(num_addresses=0)
    order = _order(lines=lines, user=user)
    if points or gift:
        card = sum((Decimal(g) for _r, g in lines), ZERO) - points - gift
        item = OrderItem.objects.filter(order=order).order_by("pk").first()
        OrderItem.objects.filter(pk=item.pk).update(
            discount_allocation=_money(points)
        )
        Order.objects.filter(pk=order.pk).update(
            loyalty_discount=_money(points),
            gift_card_amount=_money(gift),
            amount_due=_money(card),
        )
        order.payment_transactions.update(amount=_money(card))
        if points:
            PointsTransaction.objects.create(
                user=user, points=10_000, transaction_type=TransactionType.EARN
            )
            PointsTransaction.objects.create(
                user=user,
                points=-int(points * 100),
                transaction_type=TransactionType.REDEEM,
                reference_order=order,
            )
        if gift:
            GiftCardTransaction.objects.create(
                gift_card=GiftCardFactory(initial_value=_money(gift)),
                kind=GiftCardTransactionKind.REDEEM,
                amount=-gift,
                order=order,
            )
    pi = f"pi_inv_{next(_ref)}"
    PaymentTransaction.objects.filter(order=order).update(provider_reference=pi)
    Order.objects.filter(pk=order.pk).update(payment_id=pi)
    order.refresh_from_db()
    return order


class Oracle:
    """What the order's ledger says was goods, replayed in time order and
    independent of how the platform stores it. See the module doc."""

    def __init__(self, order: Order, total: Decimal):
        self.order = order
        #: What the provider's money paid for (the order's total).
        self.total = total

    def _excess(self, held: Decimal, goods: Decimal, pending: Decimal):
        stands = held - (self.total - goods)
        if stands <= PAYMENT_AMOUNT_TOLERANCE:
            return ZERO
        return max(stands - pending, ZERO)

    def goods(self, cut: datetime | None = None) -> Decimal:
        """Everything returned before *cut* (``None``: all of it): the
        provider's money that was goods, gift card value paid back, and the
        goods of a chargeback whose goods came back."""
        order = self.order
        events: list[tuple[datetime, int, int, str, object]] = []
        for row in PaymentTransaction.objects.filter(order=order):
            events.append((row.created_at, 1, row.pk, "tx", row))
        for att in RefundAttempt.objects.filter(
            order=order, kind=RefundAttemptKind.PROVIDER
        ).exclude(status=RefundAttemptStatus.DECLINED):
            if att.status == RefundAttemptStatus.SETTLED and att.refund is None:
                continue  # answered another attempt's refund: no money
            events.append((att.created_at, 0, att.pk, "claim", att))
        for dec in ChargebackDecision.objects.filter(
            chargeback__order=order, goods_returned=True
        ).select_related("chargeback"):
            events.append((dec.decided_at, 2, dec.pk, "decision", dec))
        for gc in GiftCardTransaction.objects.filter(
            order=order, kind=GiftCardTransactionKind.REFUND_CREDIT
        ):
            events.append((gc.created_at, 3, gc.pk, "gift", gc))
        events.sort(key=lambda e: e[:3])

        held = ZERO
        provider_goods = all_goods = ZERO
        #: claim pk -> (amount, the part of it that was excess)
        pending: dict[int, tuple[Decimal, Decimal]] = {}
        #: refund reference -> [amount, goods part, reversed]
        parts: dict[str, list[Decimal]] = {}
        claimed_refunds = {
            a.refund_id: a.pk
            for a in RefundAttempt.objects.filter(
                order=order, refund__isnull=False
            )
        }
        for when, _rank, _pk, kind, obj in events:
            if cut is not None and when >= cut:
                break
            if kind == "claim":
                # Judged now, against what other claims hold in flight.
                excess = self._excess(
                    held,
                    provider_goods,
                    sum((ex for _a, ex in pending.values()), ZERO),
                )
                amount = Decimal(obj.amount.amount)
                pending[obj.pk] = (amount, min(excess, amount))
            elif kind == "gift":
                all_goods += Decimal(obj.amount)
            elif kind == "decision":
                amount = Decimal(obj.chargeback.amount.amount)
                provider_goods += amount
                all_goods += amount
            else:
                row = obj
                amount = Decimal(row.amount.amount)
                if row.kind == PaymentTransactionKind.CHARGE:
                    held += amount
                elif row.kind == PaymentTransactionKind.CHARGEBACK:
                    held -= amount
                elif row.kind == PaymentTransactionKind.REFUND:
                    claim_pk = claimed_refunds.get(row.pk)
                    if claim_pk in pending:
                        _a, excess = pending.pop(claim_pk)
                    else:
                        excess = self._excess(
                            held,
                            provider_goods,
                            sum((a for a, _ex in pending.values()), ZERO),
                        )
                    goods = amount - min(excess, amount)
                    parts[row.provider_reference] = [amount, goods, ZERO]
                    provider_goods += goods
                    all_goods += goods
                    held -= amount
                elif row.kind == PaymentTransactionKind.REFUND_REVERSAL:
                    held += amount
                    part = parts.get(row.provider_reference)
                    if part is not None:
                        part[2] += amount
                        new = min(part[1], part[0] - part[2])
                        provider_goods -= part[1] - new
                        all_goods -= part[1] - new
                        part[1] = new
        return all_goods


class State:
    def __init__(self, lines, points, gift, fake, staff):
        self.fake = fake
        self.staff = staff
        self.order = _build_order(lines, points, gift)
        goods = sum((Decimal(g) for _r, g in lines), ZERO)
        #: The provider's money the order is for, and what the receipt holds.
        self.total = goods - points - gift
        self.receipt_sale = goods - points
        assert self.order.calculate_order_total_amount().amount == self.total
        self.oracle = Oracle(self.order, self.total)
        self.shipped = False
        self.log: list[str] = []
        self.viol: list[tuple[str, str]] = []
        self.reversed_after_credit = False
        self.receipt_checked = False

    # -- helpers -----------------------------------------------------------
    def o(self) -> Order:
        self.order.refresh_from_db()
        return self.order

    def charges(self):
        return list(
            PaymentTransaction.objects.filter(
                order=self.order, kind=PaymentTransactionKind.CHARGE
            ).order_by("id")
        )

    def live(self):
        return list(
            RefundAttempt.objects.live()
            .filter(order=self.order, kind=RefundAttemptKind.PROVIDER)
            .order_by("id")
        )

    # -- events ------------------------------------------------------------
    def do(self, ev, a, b):
        order = self.o()
        if ev == "dup":
            amt = _frac(self.total, a)
            PaymentLedger.record_charge(
                order,
                amount=_money(amt),
                provider="stripe",
                reference=f"pi_dup_{next(_ref)}",
            )
            self.log.append(f"dup {amt}")
        elif ev == "receipt":
            self.issue_receipt()
        elif ev in ("refund", "refund_timeout", "refund_lost", "refund_full"):
            avail = PaymentLedger.refundable_value(order)
            amt = None if ev == "refund_full" else _frac(avail, a)
            if amt is not None and amt <= 0:
                self.log.append(f"{ev} skipped (nothing refundable)")
                return
            self.fake.fail_with = (
                stripe.APIConnectionError("x")
                if ev == "refund_timeout"
                else None
            )
            self.fake.lose_answer = ev == "refund_lost"
            try:
                try:
                    res = OrderService.refund_order(
                        order, amount=None if amt is None else _money(amt)
                    )
                finally:
                    self.fake.fail_with = None
                    self.fake.lose_answer = False
                self.log.append(
                    f"{ev} {amt} -> refunded {res.refunded.amount}"
                    f" stored {res.stored.amount}"
                    f" declined={bool(res.declined)}"
                )
            except PaymentError as e:
                self.log.append(f"{ev} {amt} REFUSED: {e}")
        elif ev in ("settle_rec", "settle_wh", "settle_staff", "decline"):
            live = self.live()
            if ev == "settle_staff":
                live = [x for x in live if self.stripe_refund(x) is not None]
            if not live:
                self.log.append(f"{ev} skipped (no claim)")
                return
            self.resolve(live[int(a * len(live)) % len(live)], ev)
        elif ev == "decline_staff":
            live = [x for x in self.live() if self.stripe_refund(x) is None]
            if not live:
                self.log.append("decline_staff skipped (no claim)")
                return
            self.resolve(live[int(a * len(live)) % len(live)], ev)
        elif ev == "dashboard":
            options = [
                (c, PaymentLedger.left_on(c) - PaymentLedger.reserved_on(c))
                for c in self.charges()
            ]
            options = [(c, left) for c, left in options if left > 0]
            if not options:
                self.log.append("dashboard skipped")
                return
            c, left = options[int(b * len(options)) % len(options)]
            amt = _frac(left, a)
            PaymentLedger.record_refund(
                order,
                charge=c,
                amount=_money(amt),
                reference=f"re_dash_{next(_ref)}",
            )
            self.log.append(f"dashboard {amt} on {c.provider_reference}")
        elif ev == "reversal":
            reversed_refs = set(
                PaymentTransaction.objects.filter(
                    order=order, kind=PaymentTransactionKind.REFUND_REVERSAL
                ).values_list("provider_reference", flat=True)
            )
            rows = [
                r
                for r in PaymentTransaction.objects.filter(
                    order=order, kind=PaymentTransactionKind.REFUND
                ).order_by("id")
                if r.provider_reference not in reversed_refs
            ]
            if not rows:
                self.log.append("reversal skipped")
                return
            r = rows[int(b * len(rows)) % len(rows)]
            amt = (
                Decimal(r.amount.amount)
                if a < 0.5
                else _frac(Decimal(r.amount.amount), a)
            )
            had_credit = RetailCredit.objects.filter(refund=r).exists()
            PaymentLedger.record_refund_reversal(
                order,
                charge=r.charge,
                amount=_money(amt),
                reference=r.provider_reference,
            )
            self.log.append(
                f"reversal {amt} of {r.provider_reference} "
                f"({r.amount.amount}){' AFTER CREDIT' if had_credit else ''}"
            )
            self.reversed_after_credit |= had_credit
        elif ev == "chargeback":
            options = [
                (c, PaymentLedger.left_on(c) - PaymentLedger.reserved_on(c))
                for c in self.charges()
            ]
            options = [(c, left) for c, left in options if left > 0]
            if not options:
                self.log.append("chargeback skipped")
                return
            c, left = options[int(b * len(options)) % len(options)]
            amt = _frac(left, a)
            PaymentLedger.record_chargeback(
                order,
                charge=c,
                amount=_money(amt),
                reference=f"dp_{next(_ref)}",
            )
            self.log.append(f"chargeback {amt} on {c.provider_reference}")
        elif ev == "decide":
            if not PaymentTransaction.objects.filter(
                order=order, kind=PaymentTransactionKind.CHARGEBACK
            ).exists():
                self.log.append("decide skipped (no chargeback)")
                return
            returned = a < 0.6
            try:
                decide_chargebacks(
                    order.id, goods_returned=returned, user=self.staff
                )
                self.log.append(f"decide goods_returned={returned}")
            except ChargebackDecisionError as e:
                self.log.append(f"decide REFUSED: {e}")
        elif ev == "credits":
            ids = create_pending_credits(order.id)
            self.log.append(f"credits created {len(ids)}")

    def stripe_refund(self, att):
        """The refund Stripe made for a claim whose answer was lost."""
        entry = self.fake._by_key.get(att.provider_key)
        return entry[1] if entry else None

    def resolve(self, att, how):
        order = self.o()
        if how == "settle_rec":
            OrderService.book_share(
                order, att, att.amount, f"re_rec_{next(_ref)}", att.reason
            )
        elif how == "settle_wh":
            PaymentLedger.record_refund(
                order,
                charge=att.charge,
                amount=att.amount,
                reference=f"re_wh_{next(_ref)}",
            )
        elif how == "settle_staff":
            made = self.stripe_refund(att)
            with mock.patch(
                "order.models.refund_attempt.reconcile_after",
                return_value=timedelta(0),
            ):
                refund_reconciliation.record_outcome(
                    att, user=self.staff, settled=True, reference=made.id
                )
        elif how == "decline_staff":
            with mock.patch(
                "order.models.refund_attempt.reconcile_after",
                return_value=timedelta(0),
            ):
                refund_reconciliation.record_outcome(
                    att, user=self.staff, settled=False
                )
        else:
            refund_reconciliation._decline(att, "probe decline")
        self.log.append(f"{how} claim#{att.pk} {att.amount.amount}")

    def issue_receipt(self):
        order = self.o()
        if not self.shipped:
            _mark_shipped(order)
            self.shipped = True
        had = Invoice.objects.filter(order=order).exists()
        generate_order_invoice(order.id)
        inv = Invoice.objects.filter(order=order).first()
        if inv is not None and inv.mydata_status != MyDataStatus.CONFIRMED:
            send_invoice_to_mydata(order.id)
            inv.refresh_from_db()
        flag = bool((self.o().metadata or {}).get("receipt_deferred"))
        self.log.append(
            f"receipt -> {'exists' if had else ''} "
            f"{inv.total.amount if inv else None} deferred={flag}"
        )
        if inv is not None and not had and not self.receipt_checked:
            self.receipt_checked = True
            expect = self.receipt_sale - self.oracle.goods(inv.created_at)
            if Decimal(inv.total.amount) != expect:
                said = (
                    f"receipt {inv.total.amount} != sale {self.receipt_sale}"
                    f" - goods before it {self.receipt_sale - expect}"
                )
                self.viol.append(("inv3", said))

    # -- invariants --------------------------------------------------------
    def credited(self) -> dict[Decimal, Decimal]:
        got: dict[Decimal, Decimal] = {}
        for c in RetailCredit.objects.filter(order=self.order).exclude(
            mydata_status=MyDataStatus.CANCELED
        ):
            for r in c.vat_breakdown:
                rate = Decimal(r["rate"])
                got[rate] = got.get(rate, ZERO) + Decimal(r["gross"])
        return got

    def check_always(self, where):
        order = self.o()
        inv = Invoice.objects.filter(order=order).first()
        got = self.credited()
        if inv is not None:
            rec = {
                Decimal(r["rate"]): Decimal(r["gross"])
                for r in inv.vat_breakdown
            }
            for rate, g in got.items():
                if g > rec.get(rate, ZERO):
                    self.viol.append(
                        ("inv1", f"{where}: rate {rate} credited {g} > {rec}")
                    )
        elif got:
            self.viol.append(("inv1", f"{where}: credit without receipt"))
        # Invariant 4: a source is credited and allocated once.
        live_credits = RetailCredit.objects.filter(order=order).exclude(
            mydata_status=MyDataStatus.CANCELED
        )
        for c in live_credits.filter(refund_attempt__isnull=False):
            att = c.refund_attempt
            if (
                att.refund_id
                and live_credits.filter(refund_id=att.refund_id)
                .exclude(pk=c.pk)
                .exists()
            ):
                self.viol.append(("inv4", f"{where}: attempt+refund credited"))
        refs = list(
            RefundAttempt.objects.filter(
                order=order, refund__isnull=False
            ).values_list("refund_id", flat=True)
        )
        if len(refs) != len(set(refs)):
            self.viol.append(("inv4", f"{where}: a refund linked twice"))
        for ch in self.charges():
            if PaymentLedger.left_on(ch) < 0:
                self.viol.append(("inv4", f"{where}: charge over-refunded"))
        for att in RefundAttempt.objects.filter(order=order):
            if (
                CreditAllocation.objects.filter(refund_attempt=att).exists()
                and att.refund_id
                and CreditAllocation.objects.filter(refund=att.refund).exists()
            ):
                self.viol.append(
                    ("inv4", f"{where}: attempt and its refund both allocated")
                )

    def finish(self, final):
        fi = iter(final)
        for att in self.live():
            r = next(fi, 0.5)
            how = (
                "settle_rec"
                if r < 0.4
                else "settle_wh"
                if r < 0.6
                else "decline"
            )
            if self.stripe_refund(att) is not None:
                how = "settle_staff"  # Stripe made it: never decline it
            self.resolve(att, how)
            self.check_always(f"final-{how}")
        allocate_credit_sources()
        order = self.o()
        if not Invoice.objects.filter(order=order).exists():
            self.issue_receipt()
        allocate_credit_sources()
        create_pending_credits(order.id)
        self.check_always("final")
        order = self.o()
        if (order.metadata or {}).get("receipt_deferred"):
            self.viol.append(("inv5", "still deferred after everything"))
        if credit_allocation.undecided(order):
            self.viol.append(("inv5", "a source is still undecided"))
        inv = Invoice.objects.filter(order=order).first()
        receipt = Decimal(inv.total.amount) if inv else ZERO
        credited = sum(self.credited().values(), ZERO)
        rows = CreditAllocation.objects.filter(order=order)
        uncovered = sum((a.uncovered for a in rows), ZERO)
        understated = sum((a.understated for a in rows), ZERO)
        goods = self.oracle.goods()
        if self.reversed_after_credit:
            return  # a credit stands after a reversal: documented, not netted
        if (
            order.payment_status == PaymentStatus.REFUNDED
            and PaymentTransaction.objects.filter(
                order=order, kind=PaymentTransactionKind.CHARGEBACK
            ).exists()
            and not ChargebackDecision.objects.filter(
                chargeback__order=order, goods_returned=True
            ).exists()
        ):
            # NOT a rule this test blesses: an order whose refunds and a lost
            # dispute together took every cent is REFUNDED, and its
            # settlement credit (``create_pending_credits``) then credits
            # what the receipt still holds, whether staff decided the
            # chargeback's goods came back, kept them, or have not decided.
            # Whether a dispute the customer kept the goods of may be
            # settled that way is a business decision (PR #530, round 6).
            return
        if (
            self.receipt_sale - receipt
        ) - understated + credited + uncovered != (goods):
            said = (
                f"receipt {receipt} of {self.receipt_sale}, credited "
                f"{credited}, uncovered {uncovered}, understated "
                f"{understated} != goods {goods}"
            )
            self.viol.append(("inv2", said))


def run(lines, points, gift, seq, final, fake, staff):
    st = State(lines, points, gift, fake, staff)
    for ev, a, b in seq:
        st.do(ev, a, b)
        st.check_always(ev)
    st.finish(final)
    return st


def kinds(st) -> set[str]:
    return {k for k, _ in st.viol}


def _run_seed(seed: int):
    lines, points, gift, seq, final = gen(seed)
    staff = UserAccountFactory(is_staff=True, is_superuser=True)
    with (
        mydata_enabled(auto_submit=False),
        FakeAade(),
        FakeStripeRefunds() as fake,
    ):
        st = run(lines, points, gift, seq, final, fake, staff)
        if not st.viol:
            return None
        target = kinds(st)
        best_seq, best = seq, st
        changed = True
        while changed:
            changed = False
            for i in range(len(best_seq)):
                cand = best_seq[:i] + best_seq[i + 1 :]
                c = run(lines, points, gift, cand, final, fake, staff)
                if kinds(c) & target:
                    best_seq, best = cand, c
                    changed = True
                    break
    return (
        f"seed {seed}: lines {lines} points {points} gift {gift}\n"
        f"violations: {st.viol}\nminimal events: {best_seq}\n"
        "log:\n  " + "\n  ".join(best.log) + f"\nviolations there: {best.viol}"
    )


@pytest.mark.parametrize("seed", list(_seeds()))
def test_credit_invariants(seed):
    failure = _run_seed(seed)
    assert failure is None, failure


# ---------------------------------------------------------------------------
# Regression tests: each sequence below was found by this test's random
# events (PR #530, round 5) and reproduced on 7bfee69ad before its fix.
# ---------------------------------------------------------------------------


def _paid_order():
    order = _order(lines=(("24", "100.00"),))
    return order, order.payment_transactions.get(
        kind=PaymentTransactionKind.CHARGE
    )


def _credited_total(order) -> Decimal:
    return sum(
        (
            Decimal(r["gross"])
            for c in RetailCredit.objects.filter(order=order)
            for r in c.vat_breakdown
        ),
        ZERO,
    )


def test_a_goods_refund_before_a_duplicate_is_not_credited_again():
    """Sale 100, receipt 100, a dashboard refund of 30 (goods, credited).
    The customer then pays 100 again and that 100 is given back. The ledger
    holds 170 against an order that is still for 70: the whole 100 is the
    duplicate, so nothing more is credited (it used to read 70 as excess and
    credit the other 30 again)."""
    order, charge = _paid_order()
    with mydata_enabled(auto_submit=False), FakeAade():
        from tests.integration.order.test_mydata_lifecycle import _receipt

        _receipt(order)
        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("30.00"), reference="re_goods"
        )
        dup, _created = PaymentLedger.record_charge(
            order, amount=_eur("100.00"), provider="stripe", reference="pi_d2"
        )
        order.refresh_from_db()
        assert PaymentLedger.excess_held(order) == Decimal("100.00")
        assert order.overcharged_at is not None

        PaymentLedger.record_refund(
            order, charge=dup, amount=_eur("100.00"), reference="re_dup"
        )
        order.refresh_from_db()
        create_pending_credits(order.id)

    assert order.overcharged_at is None
    assert _credited_total(order) == Decimal("30.00")


def test_a_claim_waits_while_an_older_claim_holds_the_duplicate():
    """Sale 100 plus a duplicate 100, receipt 100. Claim C (100) times out
    and holds the excess. A refund of 99 would be decided as goods (C holds
    the excess) and stay credited if C were then declined: it is refused
    before any provider is called. Resuming C itself is allowed, and once C
    is declined the 99 is the duplicate and owes no credit."""
    order, _charge = _paid_order()
    with (
        mydata_enabled(auto_submit=False),
        FakeAade(),
        FakeStripeRefunds() as f,
    ):
        from tests.integration.order.test_mydata_lifecycle import _receipt

        _receipt(order)
        PaymentLedger.record_charge(
            order, amount=_eur("100.00"), provider="stripe", reference="pi_d3"
        )
        f.fail_with = stripe.APIConnectionError("x")
        OrderService.refund_order(order, amount=_eur("100.00"))
        f.fail_with = None
        claim = RefundAttempt.objects.live().get(order=order)

        with pytest.raises(PaymentError, match="waiting for the payment pro"):
            OrderService.refund_order(order, amount=_eur("99.00"))
        assert RefundAttempt.objects.filter(order=order).count() == 1

        refund_reconciliation._decline(claim, "declined")
        result = OrderService.refund_order(order, amount=_eur("99.00"))
        allocate_credit_sources()
        create_pending_credits(order.id)

    assert result.refunded.amount == Decimal("99.00")
    assert _credited_total(order) == ZERO
    assert not credit_allocation.undecided(order)


def test_resuming_the_claim_that_holds_the_duplicate_is_allowed():
    order, _charge = _paid_order()
    with (
        mydata_enabled(auto_submit=False),
        FakeAade(),
        FakeStripeRefunds() as f,
    ):
        PaymentLedger.record_charge(
            order, amount=_eur("100.00"), provider="stripe", reference="pi_d4"
        )
        f.fail_with = stripe.APIConnectionError("x")
        OrderService.refund_order(order, amount=_eur("100.00"))
        f.fail_with = None
        result = OrderService.refund_order(order, amount=_eur("100.00"))

    assert result.refunded.amount == Decimal("100.00")
    assert RefundAttempt.objects.filter(order=order).count() == 1


def _receipt_netted_then_reversed():
    """Sale 100, a refund of 40 before the receipt, the receipt issued net
    (60), then the provider reports the 40 failed: the customer kept the
    goods and the receipt is 40 short."""
    from tests.integration.order.test_mydata_lifecycle import _receipt

    order, charge = _paid_order()
    with mydata_enabled(auto_submit=False), FakeAade():
        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("40.00"), reference="re_40"
        )
        _receipt(order)
        PaymentLedger.record_refund_reversal(
            order, charge=charge, amount=_eur("40.00"), reference="re_40"
        )
        allocate_credit_sources()
        create_pending_credits(order.id)
    return order


def test_a_refund_reversed_after_the_receipt_netted_it_is_flagged():
    from admin.dashboard.store import widgets
    from order.models import OrderHistory
    from tests.unit.admin.dashboard.test_store_widgets import (
        _context,
        _superuser,
    )

    order = _receipt_netted_then_reversed()

    assert Invoice.objects.get(order=order).total.amount == Decimal("60.00")
    assert _credited_total(order) == ZERO
    [row] = credit_allocation.understated_orders()
    assert row.understated == Decimal("40.00")
    assert OrderHistory.objects.filter(
        order=order, new_value__note__icontains="short of the goods"
    ).exists()
    titles = [
        str(a["title"])
        for a in _context(widgets.StoreAlerts, _superuser())["alerts"]
    ]
    assert "Receipts short of goods the customer kept" in titles
    assert "Refunded money beyond the receipt" not in titles


def test_staff_acknowledge_a_receipt_left_short():
    from order.models import OrderHistory

    order = _receipt_netted_then_reversed()
    staff = UserAccountFactory(is_staff=True, is_superuser=True)

    assert credit_allocation.acknowledge_uncovered(order, user=staff) == 1

    assert not credit_allocation.uncovered_orders().exists()
    assert (
        OrderHistory.objects.filter(
            order=order, new_value__note__icontains="short of"
        ).count()
        == 2
    )


def test_a_reversal_inside_the_duplicate_leaves_the_receipt_alone():
    """The reversed refund was the duplicate: no goods were netted from the
    receipt, so nothing about it is understated."""
    from tests.integration.order.test_mydata_lifecycle import _receipt

    order, _charge = _paid_order()
    with mydata_enabled(auto_submit=False), FakeAade():
        dup, _created = PaymentLedger.record_charge(
            order, amount=_eur("100.00"), provider="stripe", reference="pi_d5"
        )
        PaymentLedger.record_refund(
            order, charge=dup, amount=_eur("100.00"), reference="re_d5"
        )
        _receipt(order)
        PaymentLedger.record_refund_reversal(
            order, charge=dup, amount=_eur("100.00"), reference="re_d5"
        )

    assert not credit_allocation.uncovered_orders().exists()
    assert Invoice.objects.get(order=order).total.amount == Decimal("100.00")


def test_a_reversal_comes_off_the_duplicate_whether_it_comes_before_or_after():
    """A refund of 80 on the duplicate payment waits (a claim in flight
    holds part of the excess), the provider reverses 70 of it, then the
    claim settles and the refund is decided. A reversal comes off the
    duplicate part first, as it does for a refund decided before it was
    reversed: the 10 that stands is goods (the 60 of the duplicate failed
    first), not a duplicate returned."""
    order, charge = _paid_order()
    with (
        mydata_enabled(auto_submit=False),
        FakeAade(),
        FakeStripeRefunds() as f,
    ):
        f.fail_with = stripe.APIConnectionError("x")
        OrderService.refund_order(order, amount=_eur("40.00"))
        f.fail_with = None
        dup, _created = PaymentLedger.record_charge(
            order, amount=_eur("100.00"), provider="stripe", reference="pi_d6"
        )
        PaymentLedger.record_refund(
            order, charge=dup, amount=_eur("80.00"), reference="re_dash"
        )
        PaymentLedger.record_refund_reversal(
            order, charge=dup, amount=_eur("70.00"), reference="re_dash"
        )
        assert credit_allocation.undecided(order)
        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("40.00"), reference="re_claim"
        )

    dash = order.payment_transactions.get(provider_reference="re_dash")
    assert not credit_allocation.undecided(order)
    assert credit_allocation.allocation_of(refund=dash) == {
        Decimal(24): Decimal("10.00")
    }
