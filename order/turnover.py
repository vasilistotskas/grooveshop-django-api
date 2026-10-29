"""Monthly turnover: net sales, less what went back, per calendar month.

Turnover here is **net of VAT, excluding shipping and the payment-method
fee, less returns**. The rule, and the reason for each part:

Which orders are sales
    An order whose payment was collected: ``payment_status`` COMPLETED,
    PARTIALLY_REFUNDED or REFUNDED (``SALE_PAYMENT_STATUSES``). The order
    ``status`` is not read. Cash on delivery counts once the courier's
    money is booked, not when the parcel ships; an order that closed
    unpaid (a canceled order, a refused parcel) has ``payment_status``
    CANCELED and is never a sale, so it needs no return either. A fully
    refunded order stays a sale in its own month and is a return in the
    month of the refund: the two net out over time and each month says
    what happened in it.

The month of a sale
    The month the order was placed, in the store's timezone
    (``settings.TIME_ZONE``, the current timezone of every request).

What a sale is worth
    The goods only, at the VAT rate and the discount snapshotted on each
    line at checkout: the same split ``order.taxation.order_vat_lines``
    gives the receipt and the myDATA payload for its ``GOODS`` lines
    (gross after the line's promotion/loyalty discount, net rounded half
    up to the cent), so the figure is the receipt's net goods total.
    Shipping and the payment-method fee are separate lines on the receipt
    and are left out. A gift card is a payment instrument and never
    reduces the taxable value (``order.discounts``), so an order paid by
    gift card is turnover in full when it is redeemed; selling the card
    is not an order and is not turnover.

Returns
    Money that went back, in the month it went back (a return belongs to
    the month it happened, not to the sale it reverses): ledger REFUND
    rows (a provider's or the merchant's own cash repayment) less
    REFUND_REVERSAL rows, value returned onto gift cards
    (``REFUND_CREDIT``), and a chargeback staff decided was for returned
    goods (``ChargebackDecision``, in the month of the decision; a lost
    dispute for goods the customer kept leaves the sale standing). These
    are the same facts the myDATA credit documents (``order.credit_documents``)
    are created from. Points restored are not counted: a loyalty redemption
    is a discount already taken off the sale, so a return of everything
    refunds the discounted receipt, which the money and gift-card shares
    already add up to.

    A refund is an amount of gross money, not a list of lines, and may
    include the shipping that was paid. It is brought to net goods the way
    a credit document spreads over the receipt: pro rata over the receipt,
    ``amount x goods net / receipt gross`` (goods after discounts, plus
    shipping and fee). Refunding the whole receipt therefore returns
    exactly the sale's net goods; a partial refund returns its share,
    rounded to the cent per refund.

Everything is aggregated in SQL (five queries however many orders there
are); a Python loop over orders would read the same figures slower.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.db.models import (
    Case,
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    Min,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce, NullIf, Round, TruncMonth
from django.utils import timezone

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import PaymentStatus

#: The payment states in which the order's money was collected.
SALE_PAYMENT_STATUSES: tuple[str, ...] = (
    PaymentStatus.COMPLETED,
    PaymentStatus.PARTIALLY_REFUNDED,
    PaymentStatus.REFUNDED,
)

_ZERO = Decimal(0)
_MONEY = DecimalField(max_digits=14, decimal_places=2)
_HUNDRED = Value(Decimal(100), output_field=_MONEY)
_ONE = Value(Decimal(1), output_field=_MONEY)


@dataclass(frozen=True)
class MonthTurnover:
    """One calendar month's figures, all in the store's currency."""

    month: date
    sales: Decimal
    returns: Decimal
    orders: int

    @property
    def net(self) -> Decimal:
        return self.sales - self.returns


def _line_gross():
    """An ``OrderItem``'s gross after its snapshotted discount."""
    return ExpressionWrapper(
        F("price") * F("quantity") - F("discount_allocation"),
        output_field=_MONEY,
    )


def _line_net():
    """Its net of VAT: ``order.taxation.split_gross`` as SQL. Both round
    half up to the cent (PostgreSQL rounds half away from zero, and a line
    is never negative)."""
    return Round(
        ExpressionWrapper(
            _line_gross() / (_ONE + F("vat_rate") / _HUNDRED),
            output_field=_MONEY,
        ),
        2,
    )


def _year_bounds(year: int) -> tuple[datetime, datetime]:
    return (
        timezone.make_aware(datetime(year, 1, 1)),
        timezone.make_aware(datetime(year + 1, 1, 1)),
    )


def _month_of(value: datetime) -> date:
    return timezone.localtime(value).date()


def _by_month(rows: QuerySet, field: str) -> QuerySet:
    return rows.annotate(month=TruncMonth(field)).values("month")


def _sales(year: int) -> tuple[dict[date, Decimal], dict[date, int]]:
    from order.models import Order, OrderItem

    start, end = _year_bounds(year)
    placed = Q(
        order__payment_status__in=SALE_PAYMENT_STATUSES,
        order__created_at__gte=start,
        order__created_at__lt=end,
    )
    net = {
        _month_of(row["month"]): row["total"]
        for row in _by_month(
            OrderItem.objects.filter(placed), "order__created_at"
        )
        .order_by()
        .annotate(total=Sum(_line_net()))
    }
    orders = {
        _month_of(row["month"]): row["total"]
        for row in _by_month(
            Order.objects.filter(
                payment_status__in=SALE_PAYMENT_STATUSES,
                created_at__gte=start,
                created_at__lt=end,
            ),
            "created_at",
        )
        .order_by()
        .annotate(total=Count("id"))
    }
    return net, orders


def _receipt_shares():
    """The order's net goods and its receipt gross, for the row's order.

    Receipt gross is the goods after discounts plus shipping and the
    payment-method fee: what a credit document spreads a refund over.
    """
    from order.models import OrderItem

    items = OrderItem.objects.filter(order=OuterRef("order_id")).order_by()
    goods_net = Coalesce(
        Subquery(
            items.values("order").annotate(v=Sum(_line_net())).values("v"),
            output_field=_MONEY,
        ),
        _ZERO,
        output_field=_MONEY,
    )
    goods_gross = Coalesce(
        Subquery(
            items.values("order").annotate(v=Sum(_line_gross())).values("v"),
            output_field=_MONEY,
        ),
        _ZERO,
        output_field=_MONEY,
    )
    receipt_gross = ExpressionWrapper(
        goods_gross
        + F("order__shipping_price")
        + F("order__payment_method_fee"),
        output_field=_MONEY,
    )
    return goods_net, receipt_gross


def _returned_net(rows: QuerySet, *, amount: str, when: str) -> dict:
    """Per month, the net goods the ``rows`` took back (see module doc)."""
    goods_net, receipt_gross = _receipt_shares()
    sign = Case(
        When(
            kind=PaymentTransactionKind.REFUND_REVERSAL,
            then=Value(-1),
        ),
        default=Value(1),
    )
    share = ExpressionWrapper(
        sign * F(amount) * goods_net / NullIf(receipt_gross, _ZERO),
        output_field=_MONEY,
    )
    return {
        _month_of(row["month"]): row["total"]
        for row in _by_month(
            rows.filter(order__payment_status__in=SALE_PAYMENT_STATUSES),
            when,
        )
        .order_by()
        .annotate(total=Sum(Round(share, 2)))
    }


def _returns(year: int) -> dict[date, Decimal]:
    from giftcard.enum import GiftCardTransactionKind
    from giftcard.models import GiftCardTransaction
    from order.models import PaymentTransaction

    start, end = _year_bounds(year)
    refunds = _returned_net(
        PaymentTransaction.objects.filter(
            kind__in=(
                PaymentTransactionKind.REFUND,
                PaymentTransactionKind.REFUND_REVERSAL,
            ),
            created_at__gte=start,
            created_at__lt=end,
        ),
        amount="amount",
        when="created_at",
    )
    gift_cards = _returned_net(
        GiftCardTransaction.objects.filter(
            kind=GiftCardTransactionKind.REFUND_CREDIT,
            order__isnull=False,
            created_at__gte=start,
            created_at__lt=end,
        ),
        amount="amount",
        when="created_at",
    )
    chargebacks = _returned_net(
        PaymentTransaction.objects.filter(
            kind=PaymentTransactionKind.CHARGEBACK,
            decision__goods_returned=True,
            decision__decided_at__gte=start,
            decision__decided_at__lt=end,
        ),
        amount="amount",
        when="decision__decided_at",
    )
    totals: dict[date, Decimal] = {}
    for source in (refunds, gift_cards, chargebacks):
        for month, value in source.items():
            totals[month] = totals.get(month, _ZERO) + (value or _ZERO)
    return totals


def turnover_by_month(year: int) -> list[MonthTurnover]:
    """The twelve months of ``year``, January first; an empty month is
    zeros, not a gap."""
    sales, orders = _sales(year)
    returns = _returns(year)
    months = [date(year, month, 1) for month in range(1, 13)]
    return [
        MonthTurnover(
            month=month,
            sales=sales.get(month, _ZERO) or _ZERO,
            returns=returns.get(month, _ZERO),
            orders=orders.get(month, 0),
        )
        for month in months
    ]


def first_year() -> int | None:
    """The year of the oldest order that counts as a sale, if any."""
    from order.models import Order

    oldest = Order.objects.filter(
        payment_status__in=SALE_PAYMENT_STATUSES
    ).aggregate(oldest=Min("created_at"))["oldest"]
    return timezone.localtime(oldest).year if oldest else None
