"""Randomized invariants of refund credit allocation (PR #530).

Every seed builds a receipt order and applies a handful of random events
through the REAL service and ledger paths, in any order:

  dup           a second payment (a duplicate the customer made)
  receipt       the order ships and its receipt is issued and submitted
  refund        ``OrderService.refund_order`` for part of what can be
                refunded; Stripe answers, times out (a claim left PENDING)
                or loses its answer (made, but nobody knows)
  refund_full   ``refund_order`` for everything
  lines*        ``refund_order`` by lines (units of a line, several lines,
                the shipping), answered, timed out or lost like ``refund``;
                ``retry_lines`` resumes the pending one under its own keys
  settle_*      a PENDING claim settled by reconciliation (``book_share``),
                by the provider's webhook (``record_refund`` with no attempt)
                or by staff ("Record refund outcome")
  decline       a PENDING claim declined, by reconciliation or by staff
  dashboard     a refund nobody claimed (``record_refund``)
  reversal      the provider reports a booked refund failed
  chargeback    a lost dispute, and later the staff decision on its goods
  credits       ``create_pending_credits``

Then every claim still PENDING is resolved, the periodic allocation runs,
every chargeback still open is decided (goods returned or kept, at random,
before or after the receipt), the receipt is issued if it never was, and the
credits are created. An independent oracle (``Oracle``) replays the order's
ledger in time order and says what the books should hold. The invariants:

1. credits never exceed the receipt, per VAT rate, at every step;
2. at rest the receipt, the credits, the money no credit could cover
   (``uncovered``) and the receipt left short of goods the customer kept
   (``understated``) each equal the oracle's own figure;
3. a receipt is net of exactly the goods part of the refunds before it;
4. nothing is booked, credited or allocated twice: at most one live credit
   per refund source (a claim and the refund it made count as one), at most
   one settlement credit, no chargeback credited twice or credited without
   its goods back, no refund reference booked twice, no refund linked to two
   claims or allocated both through its claim and itself;
5. at rest no receipt is deferred and no source (claim or chargeback) is
   undecided;
6. a refund by lines is credited at exactly the rates of its own lines (never
   a rate it did not pay for), and no line is held for more than its value;
7. a line that has had its whole value back (loyalty points included, which
   owe no credit but are value returned) is never offered for refund again.

The oracle encodes the product rules of ``docs/order-system.md`` §7 from
first principles, never through the code's formulas (see ``Oracle``). The
exemptions it keeps are each a documented rule: a claim that only answered
another claim's refund moved no money; a reversal after a credit was issued
leaves the credit standing. There are no others.

Shapes that fail before the round-7 code fix (a lost dispute still open, or
decided after a refund, is read as taking money from the duplicate payment):
a chargeback, a duplicate payment, a refund of it, and a receipt, with no
decision before the refund (seeds 21 and more).

Also failing before it: a refund nobody claimed, judged while a claim for
goods is still in flight, which took the claim's whole amount out of the
excess (netted from what the ledger holds, but not from what the order is
still for): a claim, a duplicate payment, a dashboard refund of it, then the
claim settles (seeds 539, 697, 1013, 1783, 1990). A claim holds only the
duplicate part it was judged to take when it was made.

``CREDIT_INVARIANT_SEEDS=0-999`` (or ``4,9,20-30``) runs more seeds than the
committed list; a failing seed is shrunk to a minimal event sequence before it
is reported.
"""

from __future__ import annotations

import itertools
import os
import random
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from unittest import mock

import pytest
import stripe
from django.db.models import Q
from django.utils import timezone

from giftcard.factories import GiftCardFactory
from giftcard.models import GiftCardTransaction, GiftCardTransactionKind
from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction
from order import credit_allocation, refund_reconciliation
from order.chargebacks import ChargebackDecisionError, decide_chargebacks
from order.credit_documents import create_pending_credits
from order.discounts import discounted_line_gross
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
from order.refund_lines import covered_units, held_values, pending_request
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


def _seeds() -> list[int]:
    spec = os.environ.get("CREDIT_INVARIANT_SEEDS", CI_SEEDS)
    seeds: list[int] = []
    for part in spec.split(","):
        first, _, last = part.partition("-")
        seeds.extend(range(int(first), int(last or first) + 1))
    return seeds


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
    "lines",
    "lines",
    "lines_timeout",
    "lines_lost",
    "retry_lines",
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
    # Drawn last, so the draws above keep their meaning: units per line
    # (only where the gross splits to the cent) and a shipping charge.
    shape = random.Random(f"shape-{seed}")
    out = []
    for rate, gross in lines:
        units = shape.choice((1, 1, 2, 3, 4))
        if (gross * 100) % units:
            units = 1
        out.append((rate, f"{gross:.2f}", units))
    shipping = (
        Decimal(shape.randint(100, 1500)) / 100
        if shape.random() < 0.4
        else ZERO
    )
    return tuple(out), points, gift, shipping, seq, final


def _build_order(
    lines, points: Decimal, gift: Decimal, shipping: Decimal
) -> Order:
    """A receipt order of *lines* (rate, gross, units) and *shipping*, with
    *points* of its first line paid by loyalty points and *gift* by a gift
    card; the card paid the rest and is the ledger's one charge."""
    user = UserAccountFactory(num_addresses=0)
    order = _order(
        lines=tuple((r, g) for r, g, _u in lines),
        shipping=f"{shipping:.2f}",
        user=user,
    )
    for (_r, gross, units), item in zip(
        lines, OrderItem.objects.filter(order=order).order_by("pk"), strict=True
    ):
        if units > 1:
            OrderItem.objects.filter(pk=item.pk).update(
                quantity=units, price=_money(Decimal(gross) / units)
            )
    if points or gift:
        card = (
            sum((Decimal(g) for _r, g, _u in lines), ZERO)
            + shipping
            - points
            - gift
        )
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


@dataclass
class Source:
    """One thing that returned goods: a refund (claimed or not), gift card
    value paid back, or a chargeback whose goods came back."""

    goods: Decimal
    #: ``pre``: its money moved before the receipt (the receipt is net of
    #: it); ``post``: it owes the receipt a credit; ``open``: a claim made
    #: before the receipt whose money has not moved yet.
    when: str
    covered: Decimal = ZERO
    uncovered: Decimal = ZERO
    allocated: bool = False
    #: Allocated before any receipt, against the order's own lines.
    pre_allocated: bool = False
    #: A credit was issued before a reversal: it stands whatever follows.
    frozen: bool = False


@dataclass
class Replay:
    #: What the receipt should be issued for (``None``: no receipt).
    receipt: Decimal | None
    #: Credits that should stand, per the receipt's room.
    credited: Decimal
    #: Money beyond what the receipt had room to credit.
    uncovered: Decimal
    #: Goods netted from the receipt and reversed after it.
    understated: Decimal


class Oracle:
    """What the order's ledger says was goods, replayed in time order and
    independent of how the platform stores it (``docs/order-system.md`` §7).

    The rules, from first principles:

    * a refund pays back first the duplicate-payment EXCESS as it stood at
      its moment: what is held (captures less refunds, net of the reversals
      booked before it) above what the order is still for (its total less
      the goods already returned), less what an older claim in flight holds;
    * a claim is judged when it was made; a refund nobody claimed at its own
      row; a reversal booked later changes no earlier judgement, and comes
      off the duplicate part of the refund first;
    * a lost dispute (chargeback) is never part of the excess: a goods-kept
      one never reduces what the order is still for and never eats the
      duplicate payment (owner rule), and a goods-returned one takes as much
      from what is held as it takes from what the order is for, so it nets
      to zero. A chargeback whose decision is open, or was taken after a
      refund, therefore judges that refund exactly as the decision would
      have if taken at the chargeback's own moment. Its goods are a credit
      source when staff decided they came back, at the decision;
    * before any receipt, a source whose money moved is netted from the
      receipt-to-be, capped by the order's own lines (the sale): goods
      claimed beyond what the order sold are ``uncovered``, whether or not a
      receipt is ever issued (an impossible "goods returned" decision is
      flagged for staff and the accountant);
    * gift card value paid back is goods but not provider money;
    * the receipt is net of the goods that came back before it; what comes
      back after it is credited, up to what the receipt still holds (money
      beyond that is ``uncovered``, a refund that went through anyway);
      goods netted from the receipt and reversed after it leave it
      ``understated``; a reversal of a refund whose credit was issued leaves
      the credit standing (*stands*, from the test's own observation), and
      one before the credit shrinks the credit, the uncovered part first.
    """

    def __init__(self, order: Order, total: Decimal, sale: Decimal):
        self.order = order
        #: What the provider's money paid for (the order's total).
        self.total = total
        #: What the receipt holds before anything is returned.
        self.sale = sale

    def _excess(self, held: Decimal, goods: Decimal, pending: Decimal):
        stands = held - (self.total - goods)
        if stands <= PAYMENT_AMOUNT_TOLERANCE:
            return ZERO
        return max(stands - pending, ZERO)

    def replay(
        self,
        receipt_at: datetime | None,
        stands: dict[str, list[bool]] | None = None,
    ) -> Replay:
        """The books as they should be, for a receipt issued at
        *receipt_at*; *stands* says, per refund reference and in order of
        its reversals, whether a credit for it existed at each."""
        stands = {k: list(v) for k, v in (stands or {}).items()}
        order = self.order
        events: list[tuple[datetime, int, int, str, object]] = []
        if receipt_at is not None:
            events.append((receipt_at, -1, 0, "receipt", None))
        for row in PaymentTransaction.objects.filter(order=order):
            events.append((row.created_at, 1, row.pk, "tx", row))
        for att in RefundAttempt.objects.filter(
            order=order, kind=RefundAttemptKind.PROVIDER
        ).exclude(status=RefundAttemptStatus.DECLINED):
            if att.status == RefundAttemptStatus.SETTLED and att.refund is None:
                # Documented (§7): it answered another attempt's refund, and
                # moved no money of its own.
                continue
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

        claimed_refunds = {
            a.refund_id: a.pk
            for a in RefundAttempt.objects.filter(
                order=order, refund__isnull=False
            )
        }
        held = ZERO
        provider_goods = ZERO
        receipt_total: Decimal | None = None
        room = ZERO
        understated = ZERO
        #: claim pk -> (source, the part of its amount that was excess)
        pending: dict[int, tuple[Source, Decimal]] = {}
        #: refund reference -> [source, amount, reversed so far]
        parts: dict[str, list] = {}
        waiting: list[Source] = []
        sources: list[Source] = []
        #: what the order's lines can still take before any receipt
        pre_room = self.sale

        def allocate(src: Source) -> None:
            nonlocal room
            if receipt_total is None:
                waiting.append(src)
                return
            src.covered = min(src.goods, room)
            src.uncovered = src.goods - src.covered
            room -= src.covered
            src.allocated = True

        def new_source(
            goods: Decimal, when: datetime, *, claim: bool = False
        ) -> Source:
            nonlocal pre_room
            after = receipt_at is not None and when > receipt_at
            kind = "post" if after else "open" if claim else "pre"
            src = Source(goods, kind)
            sources.append(src)
            if after:
                allocate(src)
            else:
                # Before any receipt: a claim at the moment it is made, a
                # refund nobody claimed at its own row.
                src.covered = min(goods, pre_room)
                src.uncovered = goods - src.covered
                src.pre_allocated = True
                pre_room -= src.covered
            return src

        def drop_goods(src: Source, amount: Decimal, stood: bool) -> None:
            nonlocal room, understated, pre_room
            if amount <= 0:
                return
            src.goods -= amount
            if src.when == "pre":
                if receipt_total is not None:
                    understated += amount
                elif src.pre_allocated:
                    from_uncovered = min(src.uncovered, amount)
                    src.uncovered -= from_uncovered
                    src.covered -= amount - from_uncovered
                    pre_room += amount - from_uncovered
            elif stood:
                src.frozen = True
            elif src.allocated and not src.frozen:
                from_uncovered = min(src.uncovered, amount)
                src.uncovered -= from_uncovered
                src.covered -= amount - from_uncovered
                room += amount - from_uncovered

        for when, _rank, _pk, kind, obj in events:
            if kind == "receipt":
                pre = sum(
                    (
                        s.covered if s.pre_allocated else s.goods
                        for s in sources
                        if s.when == "pre"
                    ),
                    ZERO,
                )
                receipt_total = self.sale - pre
                room = receipt_total
                for src in waiting:
                    allocate(src)
                waiting.clear()
                # A claim in flight holds its room from the receipt on, and
                # its money moves after it: it owes a credit, not a netting.
                for src in sources:
                    if src.when == "open":
                        src.when = "post"
                        allocate(src)
            elif kind == "claim":
                # Judged now, against what other claims hold in flight.
                excess = self._excess(
                    held,
                    provider_goods,
                    sum((ex for _s, ex in pending.values()), ZERO),
                )
                amount = Decimal(obj.amount.amount)
                # A refund by lines is goods returned: it takes none of a
                # duplicate payment (docs/order-system.md, refund by lines).
                excess = ZERO if obj.request_id else min(excess, amount)
                src = new_source(amount - excess, when, claim=True)
                pending[obj.pk] = (src, excess)
            elif kind == "gift":
                new_source(Decimal(obj.amount), when)
            elif kind == "decision":
                new_source(Decimal(obj.chargeback.amount.amount), when)
            else:
                row = obj
                amount = Decimal(row.amount.amount)
                if row.kind == PaymentTransactionKind.CHARGE:
                    held += amount
                elif row.kind == PaymentTransactionKind.REFUND:
                    claim_pk = claimed_refunds.get(row.pk)
                    if claim_pk in pending:
                        # Judged and allocated when it was claimed; the row
                        # only books it.
                        src, excess = pending.pop(claim_pk)
                        if src.when == "open":
                            src.when = "pre"  # it moved before the receipt
                    else:
                        excess = self._excess(
                            held,
                            provider_goods,
                            sum((ex for _s, ex in pending.values()), ZERO),
                        )
                        excess = min(excess, amount)
                        src = new_source(amount - excess, when)
                    parts[row.provider_reference] = [src, amount, ZERO]
                    provider_goods += src.goods
                    held -= amount
                elif row.kind == PaymentTransactionKind.REFUND_REVERSAL:
                    held += amount
                    part = parts.get(row.provider_reference)
                    if part is not None:
                        src, refunded, reversed_so_far = part
                        part[2] = reversed_so_far = reversed_so_far + amount
                        flags = stands.get(row.provider_reference) or [False]
                        stood = flags.pop(0)
                        keep = min(src.goods, refunded - reversed_so_far)
                        freed = src.goods - keep
                        provider_goods -= freed
                        drop_goods(src, freed, stood)
                # A CHARGEBACK row: see the class doc, it never moves the
                # excess.

        credited = sum((s.covered for s in sources if s.when == "post"), ZERO)
        uncovered = sum(
            (
                s.uncovered
                for s in sources
                if s.when == "post" or s.pre_allocated
            ),
            ZERO,
        )
        return Replay(receipt_total, credited, uncovered, understated)


class State:
    def __init__(self, lines, points, gift, shipping, fake, staff):
        self.fake = fake
        self.staff = staff
        self.shipping = shipping
        self.order = _build_order(lines, points, gift, shipping)
        goods = sum((Decimal(g) for _r, g, _u in lines), ZERO) + shipping
        #: The provider's money the order is for, and what the receipt holds.
        self.total = goods - points - gift
        self.receipt_sale = goods - points
        #: lines-refund attempt pk -> the VAT rates of its own pieces
        self.line_rates: dict[int, set[Decimal]] = {}
        assert self.order.calculate_order_total_amount().amount == self.total
        self.oracle = Oracle(self.order, self.total, self.receipt_sale)
        self.shipped = False
        self.log: list[str] = []
        self.viol: list[tuple[str, str]] = []
        #: refund reference -> per reversal, in order: did a live credit
        #: for that refund exist when it was reversed (it then stands)
        self.stands: dict[str, list[bool]] = {}
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

    def live_credits(self):
        return RetailCredit.objects.filter(order=self.order).exclude(
            mydata_status=MyDataStatus.CANCELED
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
        elif ev in ("lines", "lines_timeout", "lines_lost", "retry_lines"):
            self.refund_lines(order, ev, a, b)
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
            had_credit = (
                self.live_credits()
                .filter(Q(refund=r) | Q(refund_attempt__refund=r))
                .exists()
            )
            self.stands.setdefault(r.provider_reference, []).append(had_credit)
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

    def refund_lines(self, order, ev, a, b):
        """A refund by lines: one or two lines with some of their units, and
        the shipping now and then; or the retry of the pending one."""
        self.fake.fail_with = (
            stripe.APIConnectionError("x") if ev == "lines_timeout" else None
        )
        self.fake.lose_answer = ev == "lines_lost"
        before = set(
            RefundAttempt.objects.filter(order=order).values_list(
                "pk", flat=True
            )
        )
        try:
            self._refund_lines(order, ev, a, b)
        finally:
            self.fake.fail_with = None
            self.fake.lose_answer = False
            for att in RefundAttempt.objects.filter(
                order=order, request_id__isnull=False
            ).exclude(pk__in=before):
                self.line_rates[att.pk] = {
                    row.rate
                    for row in CreditAllocation.objects.filter(
                        refund_attempt=att, amount__gt=0
                    )
                }

    def _refund_lines(self, order, ev, a, b):
        try:
            if ev == "retry_lines":
                pending = pending_request(order)
                if pending is None:
                    self.log.append("retry_lines skipped (none pending)")
                    return
                OrderService.refund_order(
                    order, retry_request_id=pending.request_id
                )
                self.log.append(f"retry_lines {pending.request_id}")
                return
            held = held_values(order)
            gross = discounted_line_gross(order, None)
            options = []
            for item in OrderItem.objects.filter(order=order).order_by("pk"):
                left = item.quantity - covered_units(
                    gross[item.pk],
                    item.quantity,
                    held.goods.get(item.pk, ZERO),
                )
                if left > 0:
                    options.append((item, left))
            shipping = (
                self.shipping > 0 and self.shipping - held.shipping_total() > 0
            )
            if not options and not shipping:
                self.log.append(f"{ev} skipped (nothing left)")
                return
            chosen = []
            if options:
                first = int(b * len(options)) % len(options)
                picks = [first]
                if len(options) > 1 and a > 0.7:
                    picks.append((first + 1) % len(options))
                for i in picks:
                    item, left = options[i]
                    chosen.append((item, 1 + int(a * 100) % left))
            include = shipping and (not chosen or a > 0.5)
            res = OrderService.refund_order(
                order, lines=chosen, include_shipping=include
            )
            self.log.append(
                f"{ev} {[(i.pk, u) for i, u in chosen]}"
                f"{' +shipping' if include else ''} -> refunded "
                f"{res.refunded.amount} stored {res.stored.amount}"
                f" declined={bool(res.declined)}"
            )
        except PaymentError as e:
            self.log.append(f"{ev} REFUSED: {e}")

    def looked_up(self, att):
        """The automatic lookup asked the provider about *att* and could
        not decide: staff may record its outcome from now on."""
        RefundAttempt.objects.filter(pk=att.pk).update(
            looked_up_at=timezone.now()
        )
        att.refresh_from_db()

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
            self.looked_up(att)
            with mock.patch(
                "order.models.refund_attempt.reconcile_after",
                return_value=timedelta(0),
            ):
                refund_reconciliation.record_outcome(
                    att, user=self.staff, settled=True, reference=made.id
                )
        elif how == "decline_staff":
            self.looked_up(att)
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
            expect = self.oracle.replay(inv.created_at, self.stands).receipt
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
        # Invariant 4: a source is credited, booked and allocated once.
        issued = self.live_credits()
        by_source = Counter()
        for c in issued.select_related("refund_attempt"):
            if c.reason == "SETTLEMENT":
                by_source["settlement"] += 1
            elif c.chargeback_id:
                by_source[("chargeback", c.chargeback_id)] += 1
            elif c.refund_id:
                by_source[("refund", c.refund_id)] += 1
            elif c.refund_attempt_id:
                att = c.refund_attempt
                by_source[
                    ("refund", att.refund_id)
                    if att.refund_id
                    else ("attempt", att.pk)
                ] += 1
        for source, n in by_source.items():
            if n > 1:
                self.viol.append(
                    ("inv4", f"{where}: {source} credited {n} times")
                )
        for c in issued.filter(chargeback__isnull=False):
            decision = ChargebackDecision.objects.filter(
                chargeback_id=c.chargeback_id
            ).first()
            if decision is None or not decision.goods_returned:
                self.viol.append(
                    ("inv4", f"{where}: a chargeback credited, goods not back")
                )
        booked = Counter(
            PaymentTransaction.objects.filter(
                order=order, kind=PaymentTransactionKind.REFUND
            ).values_list("provider_reference", flat=True)
        )
        for reference, n in booked.items():
            if n > 1:
                self.viol.append(
                    ("inv4", f"{where}: refund {reference} booked {n} times")
                )
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
        for c in issued.select_related("refund_attempt"):
            att = c.refund_attempt or (
                RefundAttempt.objects.filter(refund_id=c.refund_id).first()
                if c.refund_id
                else None
            )
            if att is None or att.pk not in self.line_rates:
                continue
            rates = {Decimal(r["rate"]) for r in c.vat_breakdown}
            if not rates <= self.line_rates[att.pk]:
                own = self.line_rates[att.pk]
                said = f"{where}: credit for lines at {own} carries {rates}"
                self.viol.append(("inv6", said))
        held = held_values(order)
        gross = discounted_line_gross(order, None)
        for pk, value in held.goods.items():
            if value > gross[pk]:
                self.viol.append(
                    ("inv6", f"{where}: line {pk} held {value} > {gross[pk]}")
                )
        # A line that has had its whole value back is never offered again
        # (points included: value back, though no credit).
        for item in OrderItem.objects.filter(order=order):
            value = held.goods.get(item.pk, ZERO)
            if (
                value >= gross[item.pk] > 0
                and covered_units(gross[item.pk], item.quantity, value)
                < item.quantity
            ):
                said = f"{where}: line {item.pk} fully held ({value}), offered"
                self.viol.append(("inv7", said))
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
        decide_before_receipt = next(fi, 0.5) < 0.5
        returned = next(fi, 0.5) < 0.6
        if decide_before_receipt:
            self.decide_all(returned)
        if not Invoice.objects.filter(order=order).exists():
            self.issue_receipt()
        if not decide_before_receipt:
            self.decide_all(returned)
        allocate_credit_sources()
        create_pending_credits(order.id)
        self.check_always("final")
        order = self.o()
        if (order.metadata or {}).get("receipt_deferred"):
            self.viol.append(("inv5", "still deferred after everything"))
        if credit_allocation.undecided(order):
            self.viol.append(("inv5", "a source is still undecided"))
        if self.undecided_chargebacks():
            self.viol.append(("inv5", "a chargeback is still undecided"))
        inv = Invoice.objects.filter(order=order).first()
        receipt = Decimal(inv.total.amount) if inv else ZERO
        rows = CreditAllocation.objects.filter(order=order)
        got = {
            "receipt": receipt,
            "credited": sum(self.credited().values(), ZERO),
            "uncovered": sum((r.uncovered for r in rows), ZERO),
            "understated": sum((r.understated for r in rows), ZERO),
        }
        want = self.oracle.replay(inv.created_at if inv else None, self.stands)
        expected = {
            "receipt": want.receipt or ZERO,
            "credited": want.credited,
            "uncovered": want.uncovered,
            "understated": want.understated,
        }
        if got != expected:
            self.viol.append(("inv2", f"code {got} != oracle {expected}"))

    def undecided_chargebacks(self):
        return PaymentTransaction.objects.filter(
            order=self.order,
            kind=PaymentTransactionKind.CHARGEBACK,
            decision__isnull=True,
        )

    def decide_all(self, returned: bool):
        """Staff decide every chargeback still open (a decision that took
        the goods back cannot be flipped once the receipt exists)."""
        if not self.undecided_chargebacks().exists():
            return
        for answer in (returned, True):
            try:
                decide_chargebacks(
                    self.order.id, goods_returned=answer, user=self.staff
                )
            except ChargebackDecisionError:
                continue
            self.log.append(f"final decide goods_returned={answer}")
            return


def run(lines, points, gift, shipping, seq, final, fake, staff):
    st = State(lines, points, gift, shipping, fake, staff)
    for ev, a, b in seq:
        st.do(ev, a, b)
        st.check_always(ev)
    st.finish(final)
    return st


def kinds(st) -> set[str]:
    return {k for k, _ in st.viol}


def _run_seed(seed: int):
    lines, points, gift, shipping, seq, final = gen(seed)
    staff = UserAccountFactory(is_staff=True, is_superuser=True)
    with (
        mydata_enabled(auto_submit=False),
        FakeAade(),
        FakeStripeRefunds() as fake,
    ):
        st = run(lines, points, gift, shipping, seq, final, fake, staff)
        if not st.viol:
            return None
        target = kinds(st)
        best_seq, best = seq, st
        changed = True
        while changed:
            changed = False
            for i in range(len(best_seq)):
                cand = best_seq[:i] + best_seq[i + 1 :]
                c = run(lines, points, gift, shipping, cand, final, fake, staff)
                if kinds(c) & target:
                    best_seq, best = cand, c
                    changed = True
                    break
    return (
        f"seed {seed}: lines {lines} points {points} gift {gift} "
        f"shipping {shipping}\n"
        f"violations: {st.viol}\nminimal events: {best_seq}\n"
        "log:\n  " + "\n  ".join(best.log) + f"\nviolations there: {best.viol}"
    )


@pytest.mark.parametrize("seed", _seeds())
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


def test_a_claim_made_before_the_duplicate_leaves_the_duplicate_whole():
    """Sale 100. A claim of 40 times out; it was made before the duplicate
    payment existed, so the claim-time rule judges it as goods 40. The
    customer then pays 100 again and 80 of it is refunded from the
    dashboard, and the provider reverses 70 of that 80. Whether the claim
    settles (held 160, still for 60) or is declined (held 200, still for
    100) the excess is 100: the 80 refund is all duplicate, the reversal
    comes off its duplicate part, and the 10 that stands is duplicate too.
    It owes nothing and is decided at once, with no wait for the claim."""
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
        assert not credit_allocation.undecided(order)
        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("40.00"), reference="re_claim"
        )

    dash = order.payment_transactions.get(
        provider_reference="re_dash", kind=PaymentTransactionKind.REFUND
    )
    assert not credit_allocation.undecided(order)
    assert credit_allocation.allocation_of(refund=dash) == {}
    assert CreditAllocation.objects.filter(refund=dash).count() == 1


def test_a_reversal_comes_off_the_duplicate_part_of_a_refund_first():
    """Sale 100 and a duplicate payment of 100 (it exists before the claim).
    A claim of 40 times out: it takes 40 of the excess. A dashboard refund
    of 80 on the duplicate therefore waits (the excess it can take depends
    on the claim), the provider reverses 70 of it, then the claim settles.
    The 80 is 60 duplicate and 20 goods; the reversal comes off the
    duplicate part first (60) and then the goods (10), so the 10 that
    stands is goods, exactly as for a refund decided before its reversal."""
    order, _charge = _paid_order()
    with (
        mydata_enabled(auto_submit=False),
        FakeAade(),
        FakeStripeRefunds() as f,
    ):
        dup, _created = PaymentLedger.record_charge(
            order, amount=_eur("100.00"), provider="stripe", reference="pi_d6b"
        )
        f.fail_with = stripe.APIConnectionError("x")
        OrderService.refund_order(order, amount=_eur("40.00"))
        f.fail_with = None
        claim = RefundAttempt.objects.live().get(order=order)
        PaymentLedger.record_refund(
            order, charge=dup, amount=_eur("80.00"), reference="re_dash"
        )
        PaymentLedger.record_refund_reversal(
            order, charge=dup, amount=_eur("70.00"), reference="re_dash"
        )
        assert credit_allocation.undecided(order)
        PaymentLedger.record_refund(
            order,
            charge=claim.charge,
            amount=_eur("40.00"),
            reference="re_claim",
        )

    dash = order.payment_transactions.get(
        provider_reference="re_dash", kind=PaymentTransactionKind.REFUND
    )
    assert not credit_allocation.undecided(order)
    assert credit_allocation.allocation_of(refund=dash) == {
        Decimal(24): Decimal("10.00")
    }


def test_the_excess_is_at_most_what_the_provider_still_holds():
    """Sale 100 refunded in full, a duplicate payment of 40 and a lost
    dispute of 30 on it: the ledger holds 40 but the provider only 10, so
    no refund can pay back more than 10 of the duplicate."""
    order, charge = _paid_order()
    with mydata_enabled(auto_submit=False), FakeAade():
        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("100.00"), reference="re_all"
        )
        dup, _created = PaymentLedger.record_charge(
            order, amount=_eur("40.00"), provider="stripe", reference="pi_d9"
        )
        PaymentLedger.record_chargeback(
            order, charge=dup, amount=_eur("30.00"), reference="dp_9"
        )

    assert PaymentLedger.excess_held(order) == Decimal("10.00")


def test_a_kept_chargeback_on_the_sale_leaves_the_whole_duplicate_refundable():
    """The cap is money, not a rule: sale 100, a lost dispute of 30 on it
    and a duplicate payment of 100 still give an excess of 100 (the
    provider holds 170)."""
    order, charge = _paid_order()
    with mydata_enabled(auto_submit=False), FakeAade():
        PaymentLedger.record_chargeback(
            order, charge=charge, amount=_eur("30.00"), reference="dp_k"
        )
        PaymentLedger.record_charge(
            order, amount=_eur("100.00"), provider="stripe", reference="pi_dk"
        )
        order.refresh_from_db()

    assert PaymentLedger.excess_held(order) == Decimal("100.00")


@pytest.mark.parametrize("decision", [None, True, False])
def test_the_settlement_never_credits_a_chargeback_staff_did_not_decide(
    decision,
):
    """A lost dispute is credited only through staff's decision on its goods.
    Order of 219.62, a chargeback of 107.63, everything else refunded (the
    order is REFUNDED), the receipt issued net of that refund (107.63):
    with no decision the settlement waits (and the order is on the
    chargeback worklist), goods returned credits the 107.63 once, goods kept
    credits nothing, ever."""
    from order.chargebacks import decide_chargebacks
    from tests.integration.order.test_mydata_lifecycle import _receipt

    order = _order(lines=(("24", "100.00"), ("13", "119.62")))
    charge = order.payment_transactions.get(kind=PaymentTransactionKind.CHARGE)
    staff = UserAccountFactory(is_staff=True, is_superuser=True)
    with mydata_enabled(auto_submit=False), FakeAade(), FakeStripeRefunds():
        PaymentLedger.record_chargeback(
            order,
            charge=charge,
            amount=_eur("107.63"),
            reference="dp_settle",
        )
        OrderService.refund_order(order)
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        invoice = _receipt(order)
        assert invoice.total.amount == Decimal("107.63")

        create_pending_credits(order.id)
        assert _credited_total(order) == ZERO
        assert (
            Order.objects.awaiting_chargeback_decision()
            .filter(pk=order.pk)
            .exists()
        )

        if decision is not None:
            decide_chargebacks(order.id, goods_returned=decision, user=staff)
            create_pending_credits(order.id)
            create_pending_credits(order.id)

    assert _credited_total(order) == (Decimal("107.63") if decision else ZERO)
    assert not RetailCredit.objects.filter(
        order=order, reason="SETTLEMENT"
    ).exists()


# ---------------------------------------------------------------------------
# The two shapes the oracle found on the round-6 code (round 7).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("receipt_first", [True, False])
@pytest.mark.parametrize("returned", [True, False])
def test_an_open_or_late_chargeback_never_eats_the_duplicate_payment(
    receipt_first, returned
):
    """Sale 100, a lost dispute of 30, the customer pays 100 again and gets
    that 100 back, and staff decide the dispute's goods afterwards. The 100
    is the duplicate whichever way staff decide (owner rule), so the receipt
    stays 100 and 30 is credited only when the goods came back."""
    from tests.integration.order.test_mydata_lifecycle import _receipt

    order, charge = _paid_order()
    staff = UserAccountFactory(is_staff=True, is_superuser=True)
    with mydata_enabled(auto_submit=False), FakeAade(), FakeStripeRefunds():
        if receipt_first:
            _receipt(order)
        PaymentLedger.record_chargeback(
            order, charge=charge, amount=_eur("30.00"), reference="dp_open"
        )
        dup, _created = PaymentLedger.record_charge(
            order, amount=_eur("100.00"), provider="stripe", reference="pi_d7"
        )
        PaymentLedger.record_refund(
            order, charge=dup, amount=_eur("100.00"), reference="re_d7"
        )
        decide_chargebacks(order.id, goods_returned=returned, user=staff)
        if not receipt_first:
            _receipt(order)
        allocate_credit_sources()
        create_pending_credits(order.id)

    assert Invoice.objects.get(order=order).total.amount == (
        Decimal("100.00")
        if receipt_first
        else Decimal("70.00")
        if returned
        else Decimal("100.00")
    )
    assert _credited_total(order) == (
        Decimal("30.00") if receipt_first and returned else ZERO
    )


def test_a_refund_nobody_claimed_is_judged_without_netting_a_claim_in_flight():
    """Sale 100. A claim of 40 times out (it stays PENDING and will be goods).
    The customer pays 20 again and that 20 is refunded from the dashboard.
    The ledger holds 120 against an order that is still for 100, so the whole
    20 is the duplicate; the 40 in flight is neither held back from the
    ledger nor owed twice. The receipt, issued once the claim settles, is
    net of the 40 only."""
    from tests.integration.order.test_mydata_lifecycle import _receipt

    order, _charge = _paid_order()
    with (
        mydata_enabled(auto_submit=False),
        FakeAade(),
        FakeStripeRefunds() as f,
    ):
        f.fail_with = stripe.APIConnectionError("x")
        OrderService.refund_order(order, amount=_eur("40.00"))
        f.fail_with = None
        claim = RefundAttempt.objects.live().get(order=order)
        dup, _created = PaymentLedger.record_charge(
            order, amount=_eur("20.00"), provider="stripe", reference="pi_d8"
        )
        PaymentLedger.record_refund(
            order, charge=dup, amount=_eur("20.00"), reference="re_d8"
        )
        OrderService.book_share(
            order, claim, claim.amount, "re_c8", claim.reason
        )
        allocate_credit_sources()
        invoice = _receipt(order)

    assert invoice.total.amount == Decimal("60.00")
