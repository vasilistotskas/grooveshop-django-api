"""Management profit: what the store earned after what it cost to earn it.

This is **not** the statutory or tax profit (ν.4308/2014). That one comes
from the books: opening stock plus purchases less closing stock, payroll,
depreciation, financial items. The platform has none of those, so the figure
here is the one an owner can run a shop by, built only from what it records:

    net revenue                         turnover, shipping and fees included
  - cost of goods sold                  what the units that shipped cost
  = gross profit
  - payment fees                        what the providers charged
  - delivery costs                      the carriers' invoices
  = contribution
  - other expenses                      marketing, software, rent, payroll...
  = operating profit

Every input is the store's own record, never an estimate, and what is not
recorded stays visible instead of reading as zero:

Net revenue
    ``order.turnover``: the same sales, returns, shipping and fees, in the
    same periods, so the two reports cannot disagree.

Cost of goods sold
    ``OrderItem.unit_cost x quantity`` of the lines that shipped in the
    period, less ``unit_cost x quantity`` of the units a return put back in
    stock, in the period they went back (``StockLog.order_item``). A return
    that was not restocked is a write-off: its cost stays. A line sold with
    no recorded cost is not costed at all: it adds nothing here and is
    reported as ``goods_uncosted``, so the **cost coverage** (the share of
    net goods that carry a cost) says how far gross profit can be trusted.
    Until it reaches 100% gross profit, contribution and operating profit
    are an upper bound.

Payment fees
    ``PaymentTransaction.fee``, as the provider reported it, in the period of
    the payment. A payment whose provider has not reported a fee adds
    nothing and lowers the **fee coverage** (the share of online payments,
    by value, with a reported fee).

Delivery costs and other expenses
    ``Expense.amount_net``, by ``Expense.date``. Carrier invoices are the
    ``SHIPPING`` category; every other category is an operating expense.

A breakdown (by product, category, payment method, channel or VAT rate)
reports goods only, before returns, over the lines that carry a cost: fees,
expenses and returns belong to the store or the order, not to a line.

Everything is aggregated in SQL: a constant number of queries however many
orders there are.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import (
    DateField,
    DecimalField,
    ExpressionWrapper,
    F,
    Q,
    Sum,
    Value,
)

from order.enum.expense import DELIVERY_EXPENSE_CATEGORIES, ExpenseCategory
from order.enum.payment_transaction import PaymentTransactionKind
from order.taxation import ChargeKind
from order.turnover import (
    TRUNC,
    Bucket,
    GroupBy,
    GroupKey,
    a_year_earlier,
    by_period,
    figures_between,
    period_of,
    sold_lines,
    window,
)

_ZERO = Decimal(0)
_MONEY = DecimalField(max_digits=14, decimal_places=2)
_HUNDRED = Decimal(100)

#: The column each grouping reads on an ``OrderItem`` (where the cost is) and
#: on its goods ``OrderVatLine`` (where the net is): the same fact twice.
_ITEM_GROUP_FIELD = {
    GroupBy.PAYMENT_METHOD: "order__pay_way_key",
    GroupBy.CATEGORY: "product__category_id",
    GroupBy.PRODUCT: "product_id",
    GroupBy.CHANNEL: "order__attribution__source",
    GroupBy.VAT_RATE: "vat_rate",
}
_LINE_GROUP_FIELD = {
    GroupBy.PAYMENT_METHOD: "order__pay_way_key",
    GroupBy.CATEGORY: "item__product__category_id",
    GroupBy.PRODUCT: "item__product_id",
    GroupBy.CHANNEL: "order__attribution__source",
    GroupBy.VAT_RATE: "vat_rate",
}


@dataclass(frozen=True)
class Profit:
    """The profit figures of one bucket, in the store's currency."""

    period: date | None
    revenue: Decimal = _ZERO
    goods_costed: Decimal = _ZERO
    goods_uncosted: Decimal = _ZERO
    cogs: Decimal = _ZERO
    payment_fees: Decimal = _ZERO
    online_collected: Decimal = _ZERO
    online_collected_with_fee: Decimal = _ZERO
    delivery_costs: Decimal = _ZERO
    operating_costs: dict[ExpenseCategory, Decimal] = field(
        default_factory=dict
    )

    @property
    def gross_profit(self) -> Decimal:
        return self.revenue - self.cogs

    @property
    def gross_margin(self) -> Decimal | None:
        """Gross profit as a percentage of net revenue; ``None`` without
        revenue."""
        return _percent(self.gross_profit, self.revenue)

    @property
    def cost_coverage(self) -> Decimal | None:
        """The percentage of net goods sold that carry a cost; ``None``
        when no goods were sold."""
        return _percent(
            self.goods_costed, self.goods_costed + self.goods_uncosted
        )

    @property
    def fee_coverage(self) -> Decimal | None:
        """The percentage of online payments (by value) whose provider
        reported a fee; ``None`` when nothing was paid online."""
        return _percent(self.online_collected_with_fee, self.online_collected)

    @property
    def contribution(self) -> Decimal:
        return self.gross_profit - self.payment_fees - self.delivery_costs

    @property
    def other_expenses(self) -> Decimal:
        return sum(self.operating_costs.values(), _ZERO)

    @property
    def operating_profit(self) -> Decimal:
        return self.contribution - self.other_expenses


@dataclass(frozen=True)
class GroupProfit:
    """One group of a breakdown: goods only, before returns, over the lines
    that carry a cost."""

    period: date | None
    group: GroupKey
    goods_costed: Decimal = _ZERO
    goods_uncosted: Decimal = _ZERO
    cogs: Decimal = _ZERO
    units: int = 0

    @property
    def goods(self) -> Decimal:
        return self.goods_costed + self.goods_uncosted

    @property
    def gross_profit(self) -> Decimal:
        """Of the costed lines: an uncosted line has no profit to state."""
        return self.goods_costed - self.cogs

    @property
    def gross_margin(self) -> Decimal | None:
        return _percent(self.gross_profit, self.goods_costed)

    @property
    def cost_coverage(self) -> Decimal | None:
        return _percent(self.goods_costed, self.goods)


def _percent(part: Decimal, whole: Decimal) -> Decimal | None:
    if whole <= 0:
        return None
    return (part * _HUNDRED / whole).quantize(Decimal("0.1"))


def total(rows: list[Profit]) -> Profit:
    """The rows added up into one; the percentages follow from the sums."""
    categories: dict[ExpenseCategory, Decimal] = {}
    for row in rows:
        for category, amount in row.operating_costs.items():
            categories[category] = categories.get(category, _ZERO) + amount
    return Profit(
        period=None,
        operating_costs=categories,
        **{
            name: sum((getattr(row, name) for row in rows), _ZERO)
            for name in (
                "revenue",
                "goods_costed",
                "goods_uncosted",
                "cogs",
                "payment_fees",
                "online_collected",
                "online_collected_with_fee",
                "delivery_costs",
            )
        },
    )


def _goods(first, last, bucket: Bucket | None) -> dict:
    """Net goods sold per bucket, with a cost and without."""
    rows = (
        by_period(
            sold_lines(first, last, None).filter(kind=ChargeKind.GOODS),
            "order__shipped_at",
            bucket,
        )
        .order_by()
        .annotate(
            costed=Sum("net", filter=Q(item__unit_cost__isnull=False)),
            uncosted=Sum("net", filter=Q(item__unit_cost__isnull=True)),
        )
    )
    return {period_of(row): row for row in rows}


def _line_cost(prefix: str = ""):
    """What the units of an ``OrderItem`` cost: its cost times its quantity,
    or the quantity of a restock of it."""
    return ExpressionWrapper(
        F(f"{prefix}unit_cost") * F("quantity"), output_field=_MONEY
    )


def _cogs(first, last, bucket: Bucket | None) -> dict:
    """The cost of the units that shipped, less the cost of the units a
    return put back on the shelf, per bucket."""
    from order.models import OrderItem, StockLog

    shipped = {
        period_of(row): row["cost"]
        for row in by_period(
            OrderItem.objects.filter(
                order__is_deleted=False,
                order__shipped_at__gte=first,
                order__shipped_at__lt=last,
                unit_cost__isnull=False,
            ),
            "order__shipped_at",
            bucket,
        )
        .order_by()
        .annotate(cost=Sum(_line_cost()))
    }
    restocked = {
        period_of(row): row["cost"]
        for row in by_period(
            StockLog.objects.filter(
                order_item__order__is_deleted=False,
                operation_type=StockLog.OPERATION_INCREMENT,
                order_item__unit_cost__isnull=False,
                created_at__gte=first,
                created_at__lt=last,
            ),
            "created_at",
            bucket,
        )
        .order_by()
        .annotate(
            cost=Sum(
                ExpressionWrapper(
                    F("order_item__unit_cost") * F("quantity_delta"),
                    output_field=_MONEY,
                )
            )
        )
    }
    return {
        period: (shipped.get(period) or _ZERO)
        - (restocked.get(period) or _ZERO)
        for period in shipped.keys() | restocked.keys()
    }


def _fees(first, last, bucket: Bucket | None) -> dict:
    """Provider fees and the online payments they cover, per bucket."""
    from order.models import PaymentTransaction

    charge = Q(kind=PaymentTransactionKind.CHARGE)
    rows = (
        by_period(
            PaymentTransaction.objects.filter(
                order__is_deleted=False,
                created_at__gte=first,
                created_at__lt=last,
            ),
            "created_at",
            bucket,
        )
        .order_by()
        .annotate(
            fees=Sum("fee"),
            collected=Sum("amount", filter=charge),
            collected_with_fee=Sum(
                "amount", filter=charge & Q(fee__isnull=False)
            ),
        )
    )
    return {period_of(row): row for row in rows}


def _expenses(
    start: date, end: date, bucket: Bucket | None
) -> dict[date | None, dict[ExpenseCategory, Decimal]]:
    """Expense amounts per bucket and category, by the day they belong to."""
    from order.models import Expense

    period = (
        TRUNC[bucket]("date")
        if bucket
        else Value(None, output_field=DateField())
    )
    result: dict[date | None, dict[ExpenseCategory, Decimal]] = {}
    for row in (
        Expense.objects.filter(date__gte=start, date__lte=end)
        .annotate(period=period)
        .values("period", "category")
        .order_by()
        .annotate(amount=Sum("amount_net"))
    ):
        result.setdefault(row["period"], {})[
            ExpenseCategory(row["category"])
        ] = row["amount"]
    return result


def figures(start: date, end: date, bucket: Bucket | None) -> list[Profit]:
    """The profit of the local days ``start`` to ``end`` inclusive: one row
    per ``bucket`` (an empty one as zeros), or one row for the whole range
    when ``bucket`` is ``None``."""
    first, last = window(start, end)
    goods = _goods(first, last, bucket)
    cogs = _cogs(first, last, bucket)
    fees = _fees(first, last, bucket)
    expenses = _expenses(start, end, bucket)
    result = []
    for revenue in figures_between(first, last, bucket):
        period = revenue.period
        sold = goods.get(period, {})
        paid = fees.get(period, {})
        costs = expenses.get(period, {})
        result.append(
            Profit(
                period=period,
                revenue=revenue.turnover,
                goods_costed=sold.get("costed") or _ZERO,
                goods_uncosted=sold.get("uncosted") or _ZERO,
                cogs=cogs.get(period) or _ZERO,
                payment_fees=paid.get("fees") or _ZERO,
                online_collected=paid.get("collected") or _ZERO,
                online_collected_with_fee=paid.get("collected_with_fee")
                or _ZERO,
                delivery_costs=sum(
                    (
                        amount
                        for category, amount in costs.items()
                        if category in DELIVERY_EXPENSE_CATEGORIES
                    ),
                    _ZERO,
                ),
                operating_costs={
                    category: amount
                    for category, amount in costs.items()
                    if category not in DELIVERY_EXPENSE_CATEGORIES
                },
            )
        )
    return result


def figures_with_prior_year(
    start: date, end: date, bucket: Bucket
) -> tuple[list[Profit], dict[date, Profit]]:
    """``figures`` and the same range a year earlier, the prior rows keyed
    by their own first day (``a_year_earlier`` of a current row's)."""
    prior = figures(a_year_earlier(start), a_year_earlier(end), bucket)
    return figures(start, end, bucket), {
        row.period: row for row in prior if row.period
    }


def figures_by_group(
    start: date, end: date, bucket: Bucket | None, group_by: GroupBy
) -> list[GroupProfit]:
    """Goods profit per group (and bucket), biggest first: see the module
    docstring for what a breakdown counts."""
    from order.models import OrderItem

    first, last = window(start, end)
    group_field = _ITEM_GROUP_FIELD[group_by]
    line_group = _LINE_GROUP_FIELD[group_by]

    goods = {
        (period_of(row), row["group"]): row
        for row in by_period(
            sold_lines(first, last, None)
            .filter(kind=ChargeKind.GOODS)
            .annotate(group=F(line_group)),
            "order__shipped_at",
            bucket,
            "group",
        )
        .order_by()
        .annotate(
            costed=Sum("net", filter=Q(item__unit_cost__isnull=False)),
            uncosted=Sum("net", filter=Q(item__unit_cost__isnull=True)),
            units=Sum("item__quantity"),
        )
    }
    cost = {
        (period_of(row), row["group"]): row["cost"]
        for row in by_period(
            OrderItem.objects.filter(
                order__is_deleted=False,
                order__shipped_at__gte=first,
                order__shipped_at__lt=last,
                unit_cost__isnull=False,
            ).annotate(group=F(group_field)),
            "order__shipped_at",
            bucket,
            "group",
        )
        .order_by()
        .annotate(cost=Sum(_line_cost()))
    }
    rows = [
        GroupProfit(
            period=period,
            group=group,
            goods_costed=row["costed"] or _ZERO,
            goods_uncosted=row["uncosted"] or _ZERO,
            cogs=cost.get((period, group)) or _ZERO,
            units=row["units"] or 0,
        )
        for (period, group), row in goods.items()
    ]
    return sorted(rows, key=lambda row: (row.period or date.min, -row.goods))


__all__ = [
    "GroupProfit",
    "Profit",
    "figures",
    "figures_by_group",
    "figures_with_prior_year",
    "total",
]
