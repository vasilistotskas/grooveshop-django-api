"""The store dashboard's cached queries.

Each returns plain data (see ``admin.dashboard.cache``). Revenue is the
turnover of ``order.turnover``, the one definition behind the turnover
report: net of VAT, shipping and fees included, an order counted when it
left the store, returns in the month they happened. The dashboard never
adds its own.

The dashboard shows one period at a time (``PERIODS``). A period is a
whole number of calendar buckets ending with the current one - days,
weeks from Monday, or months - in the store's timezone, and every figure
for it covers exactly that span: the headline numbers and the chart bars
always add up. It is compared with the span of equal length before it.
A period query computes the one window asked for (``variants``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from django.apps import apps
from django.db.models import Count, F, Max, Min, Q, Sum
from django.db.models.functions import Length, TruncDay, TruncMonth, TruncWeek
from django.utils import timezone

from admin.dashboard.cache import dashboard_query
from order.turnover import Bucket, figures_between


@dataclass(frozen=True)
class Period:
    key: str
    count: int
    bucket: str  # "day", "week" or "month"

    def buckets(self) -> list[date]:
        """The first day of each bucket, oldest first."""
        today = timezone.localdate()
        if self.bucket == "day":
            return [
                today - timedelta(days=self.count - 1 - i)
                for i in range(self.count)
            ]
        if self.bucket == "week":
            monday = today - timedelta(days=today.weekday())
            return [
                monday - timedelta(weeks=self.count - 1 - i)
                for i in range(self.count)
            ]
        months = today.year * 12 + today.month - 1
        return [
            date(month // 12, month % 12 + 1, 1)
            for month in range(months - self.count + 1, months + 1)
        ]

    def window(self) -> tuple[datetime, datetime, datetime]:
        """``(prior_start, start, now)``: this period and the one before.

        The prior span is subtracted in UTC: an aware datetime minus a
        timedelta is wall-clock arithmetic in its own zone, so a span
        crossing a DST change would come out an hour long or short.
        """
        now = timezone.now()
        start = timezone.make_aware(
            datetime.combine(self.buckets()[0], time.min)
        ).astimezone(UTC)
        return start - (now - start), start, now

    @property
    def trunc(self):
        return {"day": TruncDay, "week": TruncWeek, "month": TruncMonth}[
            self.bucket
        ]


PERIODS: tuple[Period, ...] = (
    Period("7d", 7, "day"),
    Period("30d", 30, "day"),
    Period("13w", 13, "week"),
    Period("12m", 12, "month"),
)
PERIODS_BY_KEY = {period.key: period for period in PERIODS}
PERIOD_KEYS = tuple(PERIODS_BY_KEY)
DEFAULT_PERIOD = "30d"

# Where orders come from: this many sources by name, the rest as "Other".
ORDER_SOURCE_TOP_N = 5

# Query fragments below this length are search-as-you-type noise, not
# intent: per-keystroke rows ("P", "Po", "Pow") dwarf committed queries.
SEARCH_MIN_QUERY_LEN = 3
SEARCH_TOP_QUERIES = 5

BEST_SELLERS = 5
RECENT_ORDERS = 6

# Active products with 0 < stock < LOW_STOCK_BELOW are "replenish soon";
# stock 0 is out of stock, a different concern.
LOW_STOCK_BELOW = 10

# Contact messages carry no read state; "new" is this many days back.
NEW_MESSAGES_DAYS = 7


def _paid() -> Q:
    from order.enum.status import PaymentStatus

    return Q(payment_status=PaymentStatus.COMPLETED)


def _bucket_of(value: datetime | date) -> date:
    if isinstance(value, datetime):
        return timezone.localtime(value).date()
    return value


#: What moves the turnover: the order itself (it ships), the VAT lines it
#: was created with, and the returns booked against it.
TURNOVER_MODELS = (
    "order.Order",
    "order.OrderVatLine",
    "order.PaymentTransaction",
    "order.ChargebackDecision",
    "giftcard.GiftCardTransaction",
)


@dashboard_query(
    "store:overview:v2",
    ttl=300,
    depends_on=TURNOVER_MODELS,
    variants=PERIOD_KEYS,
)
def overview(key: str) -> dict[str, Any]:
    """The headline figures for a period and the period before it.

    Three round trips: every order figure in ONE ``aggregate()``, every
    customer figure in another, and repeat buyers. New customers are not
    a dependency (every login saves the user row); the TTL bounds them.
    Guests have no identity to repeat, so repeat buyers count accounts.
    """
    prior_start, start, now = PERIODS_BY_KEY[key].window()
    Order = apps.get_model("order", "Order")
    User = apps.get_model("user", "UserAccount")
    paid = _paid()
    current = Q(created_at__gte=start)
    prior = Q(created_at__gte=prior_start, created_at__lt=start)

    [sold] = figures_between(start, now, None)
    [prior_sold] = figures_between(prior_start, start, None)
    orders = Order.objects.filter(created_at__gte=prior_start).aggregate(
        orders=Count("id", filter=current),
        prior_orders=Count("id", filter=prior),
        paid=Count("id", filter=paid & current),
        prior_paid=Count("id", filter=paid & prior),
    )
    customers = User.objects.filter(created_at__gte=prior_start).aggregate(
        new=Count("id", filter=current),
        prior_new=Count("id", filter=prior),
    )
    buyers = (
        Order.objects.filter(paid, current, user__isnull=False)
        .values("user_id")
        .annotate(orders=Count("id"))
        .aggregate(
            paying=Count("user_id"),
            repeat=Count("user_id", filter=Q(orders__gt=1)),
        )
    )
    return {
        "revenue": float(sold.turnover),
        "prior_revenue": float(prior_sold.turnover),
        "sold": sold.orders,
        "prior_sold": prior_sold.orders,
        "orders": orders["orders"],
        "prior_orders": orders["prior_orders"],
        "paid": orders["paid"],
        "prior_paid": orders["prior_paid"],
        "new_customers": customers["new"],
        "prior_new_customers": customers["prior_new"],
        "paying_customers": buyers["paying"],
        "repeat_customers": buyers["repeat"],
    }


@dashboard_query(
    "store:sales-series:v2",
    ttl=300,
    depends_on=TURNOVER_MODELS,
    variants=PERIOD_KEYS,
)
def sales_series(key: str) -> list[dict[str, Any]]:
    """Per bucket of the period: revenue, orders sold, orders placed,
    orders paid and accounts created. Empty buckets are zeros, not
    gaps."""
    period = PERIODS_BY_KEY[key]
    _prior_start, start, now = period.window()
    Order = apps.get_model("order", "Order")
    User = apps.get_model("user", "UserAccount")
    paid = _paid()

    orders = {
        _bucket_of(row["bucket"]): row
        for row in Order.objects.filter(created_at__gte=start)
        .annotate(bucket=period.trunc("created_at"))
        .values("bucket")
        .annotate(
            orders=Count("id"),
            paid=Count("id", filter=paid),
        )
    }
    sold = {
        row.period: row
        for row in figures_between(start, now, Bucket(period.bucket))
    }
    customers = {
        _bucket_of(row["bucket"]): row["count"]
        for row in User.objects.filter(created_at__gte=start)
        .annotate(bucket=period.trunc("created_at"))
        .values("bucket")
        .annotate(count=Count("id"))
    }
    series = []
    for bucket in period.buckets():
        row = orders.get(bucket)
        shipped = sold.get(bucket)
        series.append(
            {
                "start": bucket,
                "revenue": float(shipped.turnover) if shipped else 0.0,
                "sold": shipped.orders if shipped else 0,
                "orders": row["orders"] if row else 0,
                "paid": row["paid"] if row else 0,
                "customers": customers.get(bucket, 0),
            }
        )
    return series


@dashboard_query(
    "store:best-sellers:v2",
    ttl=900,
    # A partial refund can save only the line (refunded_quantity).
    depends_on=("order.Order", "order.OrderItem"),
    per_language=True,
    variants=PERIOD_KEYS,
)
def best_sellers(key: str) -> list[dict[str, Any]]:
    """The products most units of left the store in the period, net of
    refunded units: the same moment ``order.turnover`` counts a sale."""
    _prior_start, start, _now = PERIODS_BY_KEY[key].window()
    OrderItem = apps.get_model("order", "OrderItem")
    Product = apps.get_model("product", "Product")
    top = list(
        OrderItem.objects.filter(order__shipped_at__gte=start)
        .values("product_id")
        .annotate(units=Sum(F("quantity") - F("refunded_quantity")))
        .filter(units__gt=0)
        .order_by("-units", "product_id")[:BEST_SELLERS]
    )
    products = Product.objects.prefetch_related("translations").in_bulk(
        [row["product_id"] for row in top]
    )
    return [
        {
            "id": row["product_id"],
            "name": products[row["product_id"]].safe_translation_getter(
                "name", any_language=True
            ),
            "units": row["units"],
        }
        for row in top
        if row["product_id"] in products
    ]


@dashboard_query(
    "store:funnel:v3",
    ttl=900,
    depends_on=("order.Order",),
    variants=PERIOD_KEYS,
)
def funnel(key: str) -> dict[str, int]:
    """Carts started → orders placed → orders paid, over the period.

    Placing an order deletes the cart it came from
    (``order.signals.handlers._clear_cart_for_order``), so the carts
    still in the table are the ones that did NOT become orders. Every
    cart started is therefore an order placed or a cart still holding
    items; empty carts (a visitor who added nothing) are not a start.
    Open carts are bounded by the TTL.
    """
    _prior_start, start, _now = PERIODS_BY_KEY[key].window()
    Cart = apps.get_model("cart", "Cart")
    Order = apps.get_model("order", "Order")
    orders = Order.objects.filter(created_at__gte=start).aggregate(
        orders=Count("id"),
        paid=Count("id", filter=_paid()),
    )
    open_carts = (
        Cart.objects.filter(created_at__gte=start, items__isnull=False)
        .distinct()
        .count()
    )
    return {
        "started": orders["orders"] + open_carts,
        "orders": orders["orders"],
        "paid": orders["paid"],
    }


@dashboard_query(
    "store:order-sources:v2",
    ttl=900,
    depends_on=("order.Order", "order.OrderAttribution"),
    variants=PERIOD_KEYS,
)
def order_sources(key: str) -> dict[str, Any]:
    """Orders per source in the period: the top few by name, the rest
    as one "Other" count. Orders placed before attribution existed have
    no row and are left out rather than shown as a source they did not
    have.

    Two queries however many sources: one source can arrive as several
    types (Instagram organically and from a tagged campaign) and is
    named after the first type alphabetically; ranking and the cut
    happen in the database, since sources are open-ended.
    """
    _prior_start, start, _now = PERIODS_BY_KEY[key].window()
    Order = apps.get_model("order", "Order")
    attributed = Order.objects.filter(
        created_at__gte=start, attribution__isnull=False
    )
    top = list(
        attributed.values("attribution__source")
        .annotate(
            count=Count("id"),
            source_type=Min("attribution__source_type"),
        )
        .order_by("-count", "attribution__source")[:ORDER_SOURCE_TOP_N]
    )
    total = attributed.count()
    return {
        "top": [
            {
                "source": row["attribution__source"],
                "source_type": row["source_type"],
                "count": row["count"],
            }
            for row in top
        ],
        "other": total - sum(row["count"] for row in top),
        "total": total,
    }


@dashboard_query("store:search:v2", ttl=900, variants=PERIOD_KEYS)
def search(key: str) -> dict[str, Any]:
    """Search totals, top queries and queries that found nothing.

    Empty queries (placeholder/browse requests) are excluded everywhere;
    the query lists also drop fragments under ``SEARCH_MIN_QUERY_LEN``.
    A query found nothing only if it NEVER returned results in the
    window (``MAX(results_count) = 0``): each search is logged as
    separate product and blog rows, so a per-row test would flag
    queries whose other lane found plenty. Clicks are None until click
    tracking has recorded anything, so the card shows "—" instead of a
    zero nobody measured.
    """
    from search.models import SearchClick, SearchQuery

    _prior_start, start, _now = PERIODS_BY_KEY[key].window()
    recent = SearchQuery.objects.filter(timestamp__gte=start).exclude(query="")
    meaningful = recent.annotate(qlen=Length("query")).filter(
        qlen__gte=SEARCH_MIN_QUERY_LEN
    )
    zero_texts = (
        meaningful.values("query")
        .annotate(max_results=Max("results_count"))
        .filter(max_results=0)
        .values("query")
    )
    return {
        "searches": recent.count(),
        "meaningful": meaningful.count(),
        "zero": meaningful.filter(query__in=zero_texts).count(),
        "clicks": (
            SearchClick.objects.filter(timestamp__gte=start).count()
            if SearchClick.objects.exists()
            else None
        ),
        "top_queries": list(
            meaningful.values("query")
            .annotate(count=Count("id"))
            .order_by("-count", "query")[:SEARCH_TOP_QUERIES]
        ),
        "zero_queries": list(
            meaningful.values("query")
            .annotate(count=Count("id"), max_results=Max("results_count"))
            .filter(max_results=0)
            .order_by("-count", "query")[:SEARCH_TOP_QUERIES]
            .values("query", "count")
        ),
    }


@dashboard_query("store:recent-orders:v1", ttl=300, depends_on=("order.Order",))
def recent_orders() -> list[dict[str, Any]]:
    Order = apps.get_model("order", "Order")
    return [
        {
            "id": order.id,
            "email": order.email,
            "status": order.status,
            # djmoney: ``.amount`` is the Decimal.
            "amount": float(order.amount_due.amount),
            "created_at": order.created_at,
        }
        for order in Order.objects.order_by("-created_at")[:RECENT_ORDERS]
    ]


@dashboard_query(
    "store:attention:v1",
    ttl=60,
    depends_on=(
        "order.Order",
        "order.Invoice",
        "product.ProductReview",
        "product.Product",
        "blog.BlogComment",
        "contact.Contact",
        "b2b.BusinessProfile",
        "shipping_acs.AcsShipment",
        "shipping_acs.AcsCodPayout",
        # ``ORDER_FOLLOW_UP_DAYS`` moves the follow-up count.
        "extra_settings.Setting",
    ),
)
def attention_counts() -> dict[str, int]:
    """Work that is waiting, in one cache entry: the sidebar badges and
    the dashboard's "Needs attention" list read the same numbers."""
    from b2b.enum import BusinessProfileStatus
    from order.enum.status import OrderStatus
    from order.models.invoice import MyDataStatus
    from product.enum.review import ReviewStatus
    from shipping_acs import attention as acs

    def model(label: str):
        return apps.get_model(label)

    now = timezone.now()
    return {
        "pending_orders": model("order.Order")
        .objects.filter(status=OrderStatus.PENDING)
        .count(),
        # Counted from placement, so midnight moves it with no write;
        # the TTL bounds that.
        "follow_up_orders": model("order.Order")
        .objects.needing_follow_up(now)
        .count(),
        # The operational alert emails (``ops.*`` email types) as work: a
        # store may switch an email off, so each kind that carries money or
        # a stuck parcel is counted here by the very rule the email uses.
        "stale_shipments": acs.stale_shipments(now).count(),
        "unremitted_cod": acs.unremitted_cod_shipments(now=now).count(),
        "unattributed_cod_payouts": acs.unattributed_payouts().count(),
        "canceled_still_paid": model("order.Order")
        .objects.refund_owed()
        .count(),
        # A lost dispute is money gone; whether its goods came back decides
        # if the receipt is credited, and only staff know.
        "chargebacks_to_settle": model("order.Order")
        .objects.awaiting_chargeback_decision()
        .count(),
        "low_stock": model("product.Product")
        .objects.filter(active=True, stock__gt=0, stock__lt=LOW_STOCK_BELOW)
        .count(),
        "pending_reviews": model("product.ProductReview")
        .objects.filter(status=ReviewStatus.NEW)
        .count(),
        "pending_comments": model("blog.BlogComment")
        .objects.filter(approved=False)
        .count(),
        "new_contact_messages": model("contact.Contact")
        .objects.filter(created_at__gte=now - timedelta(days=NEW_MESSAGES_DAYS))
        .count(),
        "pending_business_profiles": model("b2b.BusinessProfile")
        .objects.filter(status=BusinessProfileStatus.PENDING)
        .count(),
        # Every receipt still rejected, however old: REJECTED is
        # terminal until someone corrects the details and resends it or
        # cancels it, and either changes the status.
        "mydata_rejected": model("order.Invoice")
        .objects.filter(mydata_status=MyDataStatus.REJECTED)
        .count(),
    }
