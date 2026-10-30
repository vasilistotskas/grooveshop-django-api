"""Turnover: what the store sold, net of VAT, less what went back.

One definition behind the turnover page, its CSV and XLSX, and the store
dashboard's revenue. Turnover is the legal κύκλος εργασιών (ν.4308/2014):
the goods **and** the shipping and payment-method fee the store invoices,
net of VAT, on an accrual basis.

When a sale is earned
    The moment the order **left the store** (``Order.shipped_at``,
    stamped by ``Order.save`` the first time the order is in a dispatched
    status), for every payment method alike. The retail receipt is issued
    when the parcel ships (``order.invoicing.receipt_issuable``,
    ``docs/order-system.md`` section 7.4), and the books of a Greek store
    are accrual books (ν.4308/2014): the sale belongs to the period of its
    receipt, not to the day the courier pays the cash on delivery out, nor
    to the day an online payment cleared before the parcel left. So a
    month, once it is over, only changes by a return booked in the month
    it happened, never because a collection arrived late.

    An order canceled or refunded before it shipped is never a sale; one
    that is still being prepared is not one yet.

What is owed, not yet collected
    A sale whose payment has not arrived (cash on delivery in transit,
    ``AWAITING_PAYMENT_STATUSES``) is turnover all the same. The amount
    the customer still owes on those is reported separately, as
    ``awaiting_collection``, and leaves it when the courier pays out.
    A parcel the customer refused or never collected closes the order
    unpaid (``payment_status`` CANCELED): the sale is reversed in the
    month it closed, like any return.

What a sale is worth
    The order's ``OrderVatLine`` rows, written when the order was created
    from the same ``order.taxation.order_vat_lines`` the receipt and the
    myDATA payload are built by: goods net (each line at the rate and the
    discount snapshotted on it), shipping net and fee net (apportioned
    over the goods' rates), and the VAT on all three. Gross is net plus
    VAT. A gift card is a payment instrument and never reduces a line, so
    an order paid by gift card is turnover in full when it ships; selling
    the card is not an order and is not turnover.

Returns
    Money that went back, in the month it went back (a return belongs to
    the month it happened, not to the sale it reverses): ledger REFUND
    rows less REFUND_REVERSAL rows, value returned onto gift cards
    (``REFUND_CREDIT``), a chargeback staff decided was for returned
    goods (in the month of the decision), and an order that closed unpaid
    after shipping (in the month it closed). Points restored are not
    counted: a loyalty redemption is a discount already taken off the
    sale. A refund is gross money, brought to net and VAT pro rata over
    the receipt, ``amount x order net / receipt gross``, the way a credit
    document spreads it (``order.credit_documents``), so a refund of the
    whole receipt returns exactly the sale's net, shipping and fee
    included. A return of an order that never shipped is not counted.

An order removed from the store (soft-deleted) is in no figure.

Months and days follow the store's timezone (``settings.TIME_ZONE``, the
current timezone of every request).

Everything is aggregated in SQL: a constant number of queries however
many orders there are.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import (
    Case,
    Count,
    DateTimeField,
    DecimalField,
    ExpressionWrapper,
    F,
    Min,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
    TextChoices,
    Value,
    When,
)
from django.db.models.functions import (
    Coalesce,
    NullIf,
    Round,
    TruncDay,
    TruncMonth,
    TruncQuarter,
    TruncWeek,
)
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import PaymentStatus
from order.taxation import ChargeKind

#: The payment states in which the order's money was collected: the only
#: ones a refund can follow.
COLLECTED_PAYMENT_STATUSES: tuple[str, ...] = (
    PaymentStatus.COMPLETED,
    PaymentStatus.PARTIALLY_REFUNDED,
    PaymentStatus.REFUNDED,
)

#: A shipped order in one of these still owes its money: cash on delivery
#: in transit. CANCELED is the courier's refusal and is a return instead.
AWAITING_PAYMENT_STATUSES: tuple[str, ...] = (
    PaymentStatus.PENDING,
    PaymentStatus.PROCESSING,
    PaymentStatus.FAILED,
)

_ZERO = Decimal(0)
_MONEY = DecimalField(max_digits=14, decimal_places=2)


class Bucket(TextChoices):
    """The calendar span one row covers."""

    QUARTER = "quarter", _("Quarter")
    MONTH = "month", _("Month")
    WEEK = "week", _("Week")
    DAY = "day", _("Day")


class GroupBy(TextChoices):
    """What a breakdown splits the sales by."""

    PAYMENT_METHOD = "payment_method", _("Payment method")
    CATEGORY = "category", _("Category")
    PRODUCT = "product", _("Product")
    CHANNEL = "channel", _("Channel")
    VAT_RATE = "vat_rate", _("VAT rate")


TRUNC = {
    Bucket.QUARTER: TruncQuarter,
    Bucket.MONTH: TruncMonth,
    Bucket.WEEK: TruncWeek,
    Bucket.DAY: TruncDay,
}

#: The column each grouping reads on an ``OrderVatLine``. Category and
#: product exist only for goods lines (shipping and the fee belong to no
#: item); the others group every line of the order.
_GROUP_FIELD = {
    GroupBy.PAYMENT_METHOD: "order__pay_way_key",
    GroupBy.CATEGORY: "item__product__category_id",
    GroupBy.PRODUCT: "item__product_id",
    GroupBy.CHANNEL: "order__attribution__source",
    GroupBy.VAT_RATE: "vat_rate",
}
_ITEM_GROUPS = frozenset({GroupBy.CATEGORY, GroupBy.PRODUCT})

type GroupKey = str | int | Decimal | None


@dataclass(frozen=True)
class Row:
    """The figures of one bucket (or one group of a breakdown), in the
    store's currency. ``period`` is the first day of the bucket, ``None``
    for a breakdown of the whole range; ``group`` is ``None`` unless the
    row belongs to a breakdown, which reports sales before returns."""

    period: date | None
    group: GroupKey
    goods: Decimal = _ZERO
    shipping: Decimal = _ZERO
    fees: Decimal = _ZERO
    vat: Decimal = _ZERO
    returns_net: Decimal = _ZERO
    returns_vat: Decimal = _ZERO
    orders: int = 0
    units: int = 0
    awaiting_collection: Decimal = _ZERO

    @property
    def sales_net(self) -> Decimal:
        return self.goods + self.shipping + self.fees

    @property
    def sales_gross(self) -> Decimal:
        return self.sales_net + self.vat

    @property
    def returns_gross(self) -> Decimal:
        return self.returns_net + self.returns_vat

    @property
    def turnover(self) -> Decimal:
        """The net revenue: sales less returns, without VAT."""
        return self.sales_net - self.returns_net

    @property
    def net_vat(self) -> Decimal:
        return self.vat - self.returns_vat

    @property
    def gross(self) -> Decimal:
        return self.sales_gross - self.returns_gross

    @property
    def average_order_value(self) -> Decimal:
        """Gross sales per order: what a customer pays on average."""
        return (
            (self.sales_gross / self.orders).quantize(Decimal("0.01"))
            if self.orders
            else _ZERO
        )


def total(rows: list[Row]) -> Row:
    """The rows added up into one."""
    return Row(
        period=None,
        group=None,
        goods=sum((row.goods for row in rows), _ZERO),
        shipping=sum((row.shipping for row in rows), _ZERO),
        fees=sum((row.fees for row in rows), _ZERO),
        vat=sum((row.vat for row in rows), _ZERO),
        returns_net=sum((row.returns_net for row in rows), _ZERO),
        returns_vat=sum((row.returns_vat for row in rows), _ZERO),
        orders=sum(row.orders for row in rows),
        units=sum(row.units for row in rows),
        awaiting_collection=sum(
            (row.awaiting_collection for row in rows), _ZERO
        ),
    )


def window(start: date, end: date) -> tuple[datetime, datetime]:
    """The aware ``[from, to)`` of the local days ``start`` to ``end``
    inclusive."""
    return (
        timezone.make_aware(datetime.combine(start, time.min)),
        timezone.make_aware(
            datetime.combine(end + timedelta(days=1), time.min)
        ),
    )


def bucket_start(day: date, bucket: Bucket) -> date:
    """The first day of the bucket ``day`` falls in."""
    if bucket == Bucket.DAY:
        return day
    if bucket == Bucket.WEEK:
        return day - timedelta(days=day.weekday())
    if bucket == Bucket.MONTH:
        return day.replace(day=1)
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def _next_bucket(day: date, bucket: Bucket) -> date:
    if bucket == Bucket.DAY:
        return day + timedelta(days=1)
    if bucket == Bucket.WEEK:
        return day + timedelta(weeks=1)
    months = 3 if bucket == Bucket.QUARTER else 1
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def buckets(start: date, end: date, bucket: Bucket) -> Iterator[date]:
    """The first day of every bucket touching ``start`` to ``end``."""
    current = bucket_start(start, bucket)
    while current <= end:
        yield current
        current = _next_bucket(current, bucket)


def a_year_earlier(day: date) -> date:
    """The same calendar day a year back (29 February becomes the 28th)."""
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def local_date(value: datetime) -> date:
    return timezone.localtime(value).date()


def period_of(row: dict[str, Any]) -> date | None:
    value = row.get("period")
    return None if value is None else local_date(value)


def by_period(
    rows: QuerySet, field: str, bucket: Bucket | None, *extra: str
) -> QuerySet:
    """``rows`` grouped by the bucket of ``field`` (and ``extra``). With no
    bucket the period is a constant NULL, which Django leaves out of the
    GROUP BY, so the aggregates cover the whole queryset."""
    period = (
        TRUNC[bucket](field)
        if bucket
        else Value(None, output_field=DateTimeField())
    )
    return rows.annotate(period=period).values("period", *extra)


def sold_lines(start: datetime, end: datetime, group_by: GroupBy | None):
    from order.models import OrderVatLine

    lines = OrderVatLine.objects.filter(
        order__is_deleted=False,
        order__shipped_at__gte=start,
        order__shipped_at__lt=end,
    )
    if group_by in _ITEM_GROUPS:
        lines = lines.filter(kind=ChargeKind.GOODS)
    return lines


def _sales(
    start: datetime,
    end: datetime,
    bucket: Bucket | None,
    group_by: GroupBy | None,
) -> dict[tuple[date | None, GroupKey], dict[str, Any]]:
    lines = sold_lines(start, end, group_by)
    if group_by is not None:
        lines = lines.annotate(group=F(_GROUP_FIELD[group_by]))
    grouped = by_period(
        lines, "order__shipped_at", bucket, *(["group"] if group_by else [])
    )
    rows = grouped.order_by().annotate(
        goods=Sum("net", filter=Q(kind=ChargeKind.GOODS)),
        shipping=Sum("net", filter=Q(kind=ChargeKind.SHIPPING)),
        fees=Sum("net", filter=Q(kind=ChargeKind.PAYMENT_FEE)),
        vat_total=Sum("vat"),
        units=Sum("item__quantity", filter=Q(kind=ChargeKind.GOODS)),
        orders=Count("order_id", distinct=True),
    )
    return {(period_of(row), row.get("group")): row for row in rows}


def _orders(
    start: datetime, end: datetime, bucket: Bucket | None
) -> dict[date | None, dict[str, Any]]:
    """Orders shipped, and what is still owed on them, per bucket. Counted
    on ``Order`` itself so an order that bills nothing is still an order."""
    from order.models import Order

    shipped = Order.objects.filter(shipped_at__gte=start, shipped_at__lt=end)
    rows = (
        by_period(shipped, "shipped_at", bucket)
        .order_by()
        .annotate(
            orders=Count("id"),
            awaiting=Sum(
                "amount_due",
                filter=Q(payment_status__in=AWAITING_PAYMENT_STATUSES),
            ),
        )
    )
    return {period_of(row): row for row in rows}


def _order_totals():
    """The net and the receipt gross of the row's order, from its VAT
    lines: what a refund is spread over."""
    from order.models import OrderVatLine

    lines = OrderVatLine.objects.filter(order=OuterRef("order_id")).order_by()

    def per_order(expression):
        return Coalesce(
            Subquery(
                lines.values("order").annotate(v=Sum(expression)).values("v"),
                output_field=_MONEY,
            ),
            _ZERO,
            output_field=_MONEY,
        )

    net = per_order("net")
    return net, ExpressionWrapper(net + per_order("vat"), output_field=_MONEY)


def _returned(
    rows: QuerySet,
    *,
    amount: str,
    when: str,
    bucket: Bucket | None,
) -> QuerySet:
    """Per bucket, the ``(period, net, gross)`` the ``rows`` took back."""
    from order.models import OrderVatLine

    net, gross = _order_totals()
    sign = Case(
        When(kind=PaymentTransactionKind.REFUND_REVERSAL, then=Value(-1)),
        default=Value(1),
    )
    money = ExpressionWrapper(sign * F(amount), output_field=_MONEY)
    net_share = Round(
        ExpressionWrapper(
            money * net / NullIf(gross, _ZERO), output_field=_MONEY
        ),
        2,
    )
    sold = rows.filter(
        order__is_deleted=False,
        order__shipped_at__isnull=False,
        order__in=OrderVatLine.objects.values("order"),
    )
    return (
        by_period(sold, when, bucket)
        .order_by()
        .annotate(net_sum=Sum(net_share), gross_sum=Sum(money))
        .values_list("period", "net_sum", "gross_sum")
    )


def _returns(
    start: datetime, end: datetime, bucket: Bucket | None
) -> dict[date | None, tuple[Decimal, Decimal]]:
    from giftcard.enum import GiftCardTransactionKind
    from giftcard.models import GiftCardTransaction
    from order.models import OrderVatLine, PaymentTransaction

    sources = [
        _returned(
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
            bucket=bucket,
        ),
        _returned(
            GiftCardTransaction.objects.filter(
                kind=GiftCardTransactionKind.REFUND_CREDIT,
                order__isnull=False,
                created_at__gte=start,
                created_at__lt=end,
            ),
            amount="amount",
            when="created_at",
            bucket=bucket,
        ),
        _returned(
            PaymentTransaction.objects.filter(
                kind=PaymentTransactionKind.CHARGEBACK,
                decision__goods_returned=True,
                decision__decided_at__gte=start,
                decision__decided_at__lt=end,
            ),
            amount="amount",
            when="decision__decided_at",
            bucket=bucket,
        ),
        # An order that shipped and then closed unpaid (the parcel was
        # refused): no money moved, the whole sale is reversed.
        by_period(
            OrderVatLine.objects.filter(
                order__is_deleted=False,
                order__payment_status=PaymentStatus.CANCELED,
                order__shipped_at__isnull=False,
                order__status_updated_at__gte=start,
                order__status_updated_at__lt=end,
            ),
            "order__status_updated_at",
            bucket,
        )
        .order_by()
        .annotate(
            net_sum=Sum("net"),
            gross_sum=Sum(F("net") + F("vat"), output_field=_MONEY),
        )
        .values_list("period", "net_sum", "gross_sum"),
    ]
    totals: dict[date | None, tuple[Decimal, Decimal]] = {}
    # One round trip for all four: they share one shape.
    for moment, net, gross in sources[0].union(*sources[1:], all=True):
        period = None if moment is None else local_date(moment)
        was_net, was_gross = totals.get(period, (_ZERO, _ZERO))
        totals[period] = (
            was_net + (net or _ZERO),
            was_gross + (gross or _ZERO),
        )
    return totals


def figures(
    start: date,
    end: date,
    bucket: Bucket | None,
    group_by: GroupBy | None = None,
) -> list[Row]:
    """The figures for the local days ``start`` to ``end`` inclusive; see
    ``figures_between``."""
    first, last = window(start, end)
    return figures_between(first, last, bucket, group_by)


def figures_between(
    first: datetime,
    last: datetime,
    bucket: Bucket | None,
    group_by: GroupBy | None = None,
) -> list[Row]:
    """The figures for the aware span ``first`` (inclusive) to ``last``.

    Without ``group_by``: one row per ``bucket`` (every bucket of the
    range, an empty one as zeros; a single row for the whole range when
    ``bucket`` is ``None``), with returns. With ``group_by``: one row per
    group (and bucket), biggest first, as sales before returns, since a
    refund is not split by product, category or rate.
    """
    sales = _sales(first, last, bucket, group_by)

    if group_by is not None:
        rows = [
            Row(
                period=period,
                group=group,
                goods=values["goods"] or _ZERO,
                shipping=values["shipping"] or _ZERO,
                fees=values["fees"] or _ZERO,
                vat=values["vat_total"] or _ZERO,
                orders=values["orders"],
                units=values["units"] or 0,
            )
            for (period, group), values in sales.items()
        ]
        return sorted(
            rows,
            key=lambda row: (row.period or date.min, -row.sales_net),
        )

    orders = _orders(first, last, bucket)
    returns = _returns(first, last, bucket)
    periods: list[date | None] = (
        list(
            buckets(
                local_date(first),
                local_date(last - timedelta(microseconds=1)),
                bucket,
            )
        )
        if bucket
        else [None]
    )
    result = []
    for period in periods:
        values = sales.get((period, None), {})
        order = orders.get(period, {})
        net, gross = returns.get(period, (_ZERO, _ZERO))
        result.append(
            Row(
                period=period,
                group=None,
                goods=values.get("goods") or _ZERO,
                shipping=values.get("shipping") or _ZERO,
                fees=values.get("fees") or _ZERO,
                vat=values.get("vat_total") or _ZERO,
                returns_net=net,
                returns_vat=gross - net,
                orders=order.get("orders", 0),
                units=values.get("units") or 0,
                awaiting_collection=order.get("awaiting") or _ZERO,
            )
        )
    return result


def figures_with_prior_year(
    start: date, end: date, bucket: Bucket
) -> tuple[list[Row], dict[date, Row]]:
    """``figures`` and the same range a year earlier, the prior rows keyed
    by their own first day (``a_year_earlier`` of a current row's)."""
    prior = figures(a_year_earlier(start), a_year_earlier(end), bucket)
    return figures(start, end, bucket), {
        row.period: row for row in prior if row.period
    }


def first_year() -> int | None:
    """The year of the oldest order that left the store, if any."""
    from order.models import Order

    oldest = Order.objects.aggregate(oldest=Min("shipped_at"))["oldest"]
    return timezone.localtime(oldest).year if oldest else None


__all__ = [
    "AWAITING_PAYMENT_STATUSES",
    "COLLECTED_PAYMENT_STATUSES",
    "Bucket",
    "GroupBy",
    "Row",
    "a_year_earlier",
    "bucket_start",
    "buckets",
    "figures",
    "figures_between",
    "figures_with_prior_year",
    "first_year",
    "total",
    "window",
]
