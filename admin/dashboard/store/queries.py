"""The store dashboard's cached queries.

Each returns plain data (see ``admin.dashboard.cache``). Windows are
rolling from "now"; revenue is money that landed - orders whose payment
COMPLETED - everywhere it appears.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.apps import apps
from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.db.models.functions import Length, TruncDay
from django.utils import timezone

from admin.dashboard.cache import dashboard_query

# Selectable windows for the revenue card: (key, days). Each is compared
# with the preceding window of equal length.
REVENUE_PERIODS: tuple[tuple[str, int], ...] = (
    ("7d", 7),
    ("30d", 30),
    ("90d", 90),
    ("365d", 365),
)

# The order-source doughnut names this many sources and folds the rest
# into "Other" - past that, slices are too thin to read.
ORDER_SOURCE_TOP_N = 6

# Days on the performance and search-volume charts.
CHART_DAYS = 14

# Query fragments below this length are search-as-you-type noise, not
# intent: per-keystroke rows ("P", "Po", "Pow") dwarf committed queries.
SEARCH_MIN_QUERY_LEN = 3
SEARCH_TOP_QUERIES = 10

# Active products with 0 < stock < LOW_STOCK_BELOW are "replenish soon";
# stock 0 is out of stock, a different concern.
LOW_STOCK_BELOW = 10


def _days() -> list[date]:
    """The last ``CHART_DAYS`` dates in the store's timezone, oldest first.

    Local, not UTC: ``TruncDay`` and ``__date`` group in the current
    timezone, so UTC dates would misplace every order placed between
    local midnight and UTC midnight.
    """
    today = timezone.localdate()
    return [
        today - timedelta(days=CHART_DAYS - 1 - i) for i in range(CHART_DAYS)
    ]


@dashboard_query("store:kpis:v1", ttl=300, depends_on=("order.Order",))
def kpis() -> dict[str, Any]:
    """Revenue per selectable window, pending orders, customers, AOV.

    Two round trips: every order figure is a filtered aggregate in ONE
    ``aggregate()``, every customer figure in another. New customers are
    not a dependency (every login saves the user row); the TTL bounds
    them.
    """
    from order.enum.status import OrderStatus, PaymentStatus

    Order = apps.get_model("order", "Order")
    User = apps.get_model("user", "UserAccount")

    now = timezone.now()
    month_ago = now - timedelta(days=30)
    paid = Q(payment_status=PaymentStatus.COMPLETED)

    aggregates: dict[str, Any] = {
        "pending": Count("id", filter=Q(status=OrderStatus.PENDING)),
        "aov": Avg("paid_amount", filter=paid & Q(created_at__gte=month_ago)),
    }
    for key, days in REVENUE_PERIODS:
        start = now - timedelta(days=days)
        prior_start = now - timedelta(days=days * 2)
        aggregates[f"current_{key}"] = Sum(
            "paid_amount", filter=paid & Q(created_at__gte=start)
        )
        aggregates[f"prior_{key}"] = Sum(
            "paid_amount",
            filter=paid & Q(created_at__gte=prior_start, created_at__lt=start),
        )
    orders = Order.objects.aggregate(**aggregates)

    customers = User.objects.aggregate(
        month=Count("id", filter=Q(created_at__gte=month_ago)),
        today=Count("id", filter=Q(created_at__date=timezone.localdate())),
    )

    revenue = {}
    for key, _days_count in REVENUE_PERIODS:
        current = float(orders[f"current_{key}"] or 0)
        prior = float(orders[f"prior_{key}"] or 0)
        revenue[key] = {
            "amount": current,
            # None when there is nothing to compare against.
            "trend_pct": (
                round((current - prior) / prior * 100, 1) if prior else None
            ),
        }

    return {
        "revenue": revenue,
        "pending_orders": orders["pending"],
        "new_customers_30d": customers["month"],
        "new_customers_today": customers["today"],
        "avg_order_value": round(float(orders["aov"] or 0), 2),
    }


@dashboard_query("store:performance:v1", ttl=300, depends_on=("order.Order",))
def performance() -> list[dict[str, Any]]:
    """Orders placed and revenue landed per day, last ``CHART_DAYS``."""
    from order.enum.status import PaymentStatus

    Order = apps.get_model("order", "Order")
    days = _days()

    by_day = {
        row["day"].date(): row
        for row in Order.objects.filter(
            created_at__date__gte=days[0],
        )
        .annotate(day=TruncDay("created_at"))
        .values("day")
        .annotate(
            orders=Count("id"),
            revenue=Sum(
                "paid_amount",
                filter=Q(payment_status=PaymentStatus.COMPLETED),
            ),
        )
    }
    return [
        {
            "day": day,
            "orders": by_day[day]["orders"] if day in by_day else 0,
            "revenue": (
                float(by_day[day]["revenue"] or 0) if day in by_day else 0.0
            ),
        }
        for day in days
    ]


@dashboard_query("store:order-status:v1", ttl=300, depends_on=("order.Order",))
def order_status() -> list[dict[str, Any]]:
    Order = apps.get_model("order", "Order")
    return list(
        Order.objects.values("status")
        .annotate(count=Count("id"))
        .order_by("-count", "status")
    )


@dashboard_query(
    "store:order-source:v1",
    ttl=300,
    depends_on=("order.Order", "order.OrderAttribution"),
)
def order_source() -> dict[str, Any]:
    """Last 30 days' orders per source: the top few by name, the rest as
    one "Other" count. Orders placed before attribution existed have no
    row and are left out rather than shown as a source they did not have.

    Two queries however many sources: one source can arrive as several
    types (Instagram organically and from a tagged campaign) and is
    named after the first type alphabetically; ranking and the cut
    happen in the database, since sources are open-ended.
    """
    Order = apps.get_model("order", "Order")
    attributed = Order.objects.filter(
        created_at__gte=timezone.now() - timedelta(days=30),
        attribution__isnull=False,
    )
    top = list(
        attributed.values("attribution__source")
        .annotate(
            count=Count("id"),
            source_type=Min("attribution__source_type"),
        )
        .order_by("-count", "attribution__source")[:ORDER_SOURCE_TOP_N]
    )
    other = attributed.count() - sum(row["count"] for row in top)
    return {
        "top": [
            {
                "source": row["attribution__source"],
                "source_type": row["source_type"],
                "count": row["count"],
            }
            for row in top
        ],
        "other": other,
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
            "amount": float(
                order.paid_amount.amount if order.paid_amount else 0
            ),
            "created_at": order.created_at,
        }
        for order in Order.objects.order_by("-created_at")[:6]
    ]


@dashboard_query(
    "store:pending-reviews:v1",
    ttl=300,
    depends_on=("product.ProductReview",),
    per_language=True,
)
def pending_reviews() -> list[dict[str, Any]]:
    from product.enum.review import ReviewStatus

    ProductReview = apps.get_model("product", "ProductReview")
    reviews = (
        ProductReview.objects.filter(status=ReviewStatus.NEW)
        .select_related("product", "user")
        .prefetch_related("product__translations")
        .order_by("-created_at")[:5]
    )
    return [
        {
            "id": review.id,
            "product": review.product.safe_translation_getter(
                "name", any_language=True
            ),
            "user_email": review.user.email if review.user else None,
            "rate": review.rate,
            "created_at": review.created_at,
        }
        for review in reviews
    ]


@dashboard_query(
    "store:contact-messages:v1", ttl=300, depends_on=("contact.Contact",)
)
def contact_messages() -> list[dict[str, Any]]:
    Contact = apps.get_model("contact", "Contact")
    return [
        {
            "id": message.id,
            "name": message.name,
            "email": message.email,
            "message": message.message,
            "created_at": message.created_at,
        }
        for message in Contact.objects.order_by("-created_at")[:5]
    ]


@dashboard_query("store:funnel:v2", ttl=900, depends_on=("order.Order",))
def funnel() -> dict[str, int]:
    """Carts started → orders placed → orders paid, over 30 days.

    Placing an order deletes the cart it came from
    (``order.signals.handlers._clear_cart_for_order``), so the carts
    still in the table are the ones that did NOT become orders. Every
    cart started is therefore an order placed or a cart still holding
    items; empty carts (a visitor who added nothing) are not a start.
    Open carts are bounded by the TTL.
    """
    from order.enum.status import PaymentStatus

    Cart = apps.get_model("cart", "Cart")
    Order = apps.get_model("order", "Order")
    month_ago = timezone.now() - timedelta(days=30)
    orders = Order.objects.filter(created_at__gte=month_ago).aggregate(
        orders=Count("id"),
        paid=Count("id", filter=Q(payment_status=PaymentStatus.COMPLETED)),
    )
    open_carts = (
        Cart.objects.filter(created_at__gte=month_ago, items__isnull=False)
        .distinct()
        .count()
    )
    return {
        "started": orders["orders"] + open_carts,
        "orders": orders["orders"],
        "paid": orders["paid"],
    }


@dashboard_query("store:retention:v1", ttl=900, depends_on=("order.Order",))
def retention() -> dict[str, int]:
    """Customers with a paid order in 30 days, and those with two or more.

    Guests (no user) are left out: they have no identity to repeat.
    """
    from order.enum.status import PaymentStatus

    Order = apps.get_model("order", "Order")
    per_customer = (
        Order.objects.filter(
            created_at__gte=timezone.now() - timedelta(days=30),
            payment_status=PaymentStatus.COMPLETED,
            user__isnull=False,
        )
        .values("user_id")
        .annotate(orders=Count("id"))
    )
    return per_customer.aggregate(
        paying=Count("user_id"),
        repeat=Count("user_id", filter=Q(orders__gt=1)),
    )


@dashboard_query(
    "store:top-products:v1",
    ttl=900,
    per_language=True,
)
def top_products() -> list[dict[str, Any]]:
    """Five active products by all-time views (a counter: TTL only)."""
    Product = apps.get_model("product", "Product")
    return [
        {
            "id": product.id,
            "name": product.safe_translation_getter("name", any_language=True),
            "views": product.view_count or 0,
        }
        for product in Product.objects.filter(active=True)
        .order_by("-view_count", "id")
        .prefetch_related("translations")[:5]
    ]


@dashboard_query("store:search:v1", ttl=900)
def search_insights() -> dict[str, Any]:
    """Search KPIs, top and zero-result queries, daily volume.

    Empty queries (placeholder/browse requests) are excluded everywhere;
    the query tables also drop fragments under ``SEARCH_MIN_QUERY_LEN``.
    A query is zero-result only if it NEVER returned results in the
    window (``MAX(results_count) = 0``): each search is logged as
    separate product/blog/federated rows, so a per-row test would flag
    queries whose other lane found plenty. Click figures are None until
    click tracking has recorded anything, so the widget shows "—"
    instead of a zero nobody measured.
    """
    from search.models import SearchClick, SearchQuery

    now = timezone.now()
    month_ago = now - timedelta(days=30)
    recent = SearchQuery.objects.filter(timestamp__gte=month_ago).exclude(
        query=""
    )
    meaningful = recent.annotate(qlen=Length("query")).filter(
        qlen__gte=SEARCH_MIN_QUERY_LEN
    )
    zero_texts = (
        meaningful.values("query")
        .annotate(max_results=Max("results_count"))
        .filter(max_results=0)
        .values("query")
    )
    has_any_click = SearchClick.objects.exists()
    days = _days()
    by_day = {
        row["day"].date(): row["count"]
        for row in SearchQuery.objects.filter(timestamp__date__gte=days[0])
        .exclude(query="")
        .annotate(day=TruncDay("timestamp"))
        .values("day")
        .annotate(count=Count("id"))
    }
    return {
        "searches": recent.count(),
        "meaningful": meaningful.count(),
        "zero": meaningful.filter(query__in=zero_texts).count(),
        "clicks": (
            SearchClick.objects.filter(timestamp__gte=month_ago).count()
            if has_any_click
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
        "volume": [{"day": day, "count": by_day.get(day, 0)} for day in days],
    }


@dashboard_query(
    "store:sidebar-counts:v1",
    ttl=60,
    depends_on=(
        "order.Order",
        "product.ProductReview",
        "product.Product",
        "blog.BlogComment",
        "blog.BlogPost",
        "contact.Contact",
        "b2b.BusinessProfile",
        "promotion.Promotion",
    ),
)
def sidebar_counts() -> dict[str, int]:
    """Every sidebar badge's count, in one cache entry.

    Carts are not a dependency (a cart is saved on every storefront
    change); the 60 s TTL bounds the abandoned-carts figure.
    """
    from b2b.enum import BusinessProfileStatus
    from order.enum.status import OrderStatus
    from product.enum.review import ReviewStatus

    def model(label: str):
        return apps.get_model(label)

    now = timezone.now()
    return {
        "pending_orders": model("order.Order")
        .objects.filter(status=OrderStatus.PENDING)
        .count(),
        "pending_reviews": model("product.ProductReview")
        .objects.filter(status=ReviewStatus.NEW)
        .count(),
        "pending_comments": model("blog.BlogComment")
        .objects.filter(approved=False)
        .count(),
        # No read state exists on a message, so "new" is the Contact
        # admin's own "This Week" window.
        "new_contact_messages": model("contact.Contact")
        .objects.filter(created_at__gte=now - timedelta(days=7))
        .count(),
        "low_stock": model("product.Product")
        .objects.filter(active=True, stock__gt=0, stock__lt=LOW_STOCK_BELOW)
        .count(),
        "abandoned_carts": model("cart.Cart")
        .objects.filter(
            updated_at__lt=now - timedelta(hours=24),
            updated_at__gte=now - timedelta(days=30),
        )
        .count(),
        "pending_business_profiles": model("b2b.BusinessProfile")
        .objects.filter(status=BusinessProfileStatus.PENDING)
        .count(),
        "draft_blog_posts": model("blog.BlogPost")
        .objects.filter(is_published=False)
        .count(),
        "live_promotions": model("promotion.Promotion").objects.live().count(),
    }
