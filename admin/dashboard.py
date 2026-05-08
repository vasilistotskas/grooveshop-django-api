"""Webside admin dashboard data layer.

Builds the data dict consumed by ``core/templates/admin/index.html``
in four zones:

A. Hero KPIs (4 cards)
B. Operations charts (revenue+orders bar/line, status doughnut)
C. Action queues (recent orders, pending reviews, contact messages)
D. System warnings (superuser-only — seller config, MyDATA, low stock,
   failed Celery tasks)

Zones A/B/C are request-independent and are cached in Redis for 5 min.
Zone D is computed fresh per request because operational alerts must
reflect the latest state. All visible strings are wrapped with
``gettext_lazy`` so the dashboard renders fully in Greek when the
admin is browsed under ``django_language=el``.
"""

from __future__ import annotations

from datetime import timedelta

from django.apps import apps
from django.core.cache import cache
from django.db.models import Avg, Count, Sum
from django.db.models.functions import TruncDay
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape, format_html
from django.utils.translation import gettext_lazy as _

DASHBOARD_CACHE_KEY = "admin:dashboard:data:v2"
DASHBOARD_CACHE_TTL = 300  # 5 minutes

# Stock-mandatory seller fields surfaced as Zone D banners.
_REQUIRED_SELLER_SETTINGS = (
    ("INVOICE_SELLER_NAME", _("Company name")),
    ("INVOICE_SELLER_VAT_ID", _("VAT ID (ΑΦΜ)")),
    ("INVOICE_SELLER_TAX_OFFICE", _("Tax office (ΔΟΥ)")),
)


def dashboard_callback(request, context):
    """Populate ``context`` with the four-zone dashboard payload.

    Zones A/B/C are served from the Redis cache (busted on writes via
    ``admin/signals.py``); Zone D is recomputed on every request so
    fixing a missing setting reflects on the next page load.
    """

    payload = cache.get_or_set(
        DASHBOARD_CACHE_KEY, _build_zones_a_b_c, DASHBOARD_CACHE_TTL
    )
    context.update(payload)
    context["is_superuser"] = bool(
        getattr(request.user, "is_authenticated", False)
        and getattr(request.user, "is_superuser", False)
    )
    if context["is_superuser"]:
        context.update(_build_zone_d())
    else:
        # Defaults so the template can `{% if seller_config_warnings %}`
        # without checking superuser flag.
        context["seller_config_warnings"] = []
        context["mydata_warnings"] = {
            "enabled": False,
            "missing_creds": [],
            "recent_rejected": 0,
            "environment": "",
        }
        context["low_stock_products"] = []
        context["failed_celery_count"] = 0
    return context


# ── Zone D — fresh, superuser-only ─────────────────────────────────────


def _build_zone_d() -> dict:
    return {
        "seller_config_warnings": _check_seller_config(),
        "mydata_warnings": _check_mydata_state(),
        "low_stock_products": _check_low_stock(),
        "failed_celery_count": _check_failed_celery(),
    }


def _check_seller_config() -> list[dict]:
    """Empty required INVOICE_SELLER_* settings — red banner."""

    from extra_settings.models import Setting

    warnings = []
    for key, label in _REQUIRED_SELLER_SETTINGS:
        value = Setting.get(key, default="")
        if not value:
            warnings.append({"key": key, "label": label})
    return warnings


def _check_mydata_state() -> dict:
    """Compile myDATA-specific alert state for the dashboard banner."""

    from extra_settings.models import Setting

    from order.models.invoice import Invoice, MyDataStatus

    enabled = bool(Setting.get("MYDATA_ENABLED", default=False))
    user_id = str(Setting.get("MYDATA_USER_ID", default="") or "")
    subscription_key = str(
        Setting.get("MYDATA_SUBSCRIPTION_KEY", default="") or ""
    )
    environment = str(Setting.get("MYDATA_ENVIRONMENT", default="dev") or "dev")

    missing_creds: list[str] = []
    if enabled and not user_id:
        missing_creds.append("MYDATA_USER_ID")
    if enabled and not subscription_key:
        missing_creds.append("MYDATA_SUBSCRIPTION_KEY")

    recent_rejected = 0
    if enabled:
        week_ago = timezone.now() - timedelta(days=7)
        recent_rejected = Invoice.objects.filter(
            mydata_status=MyDataStatus.REJECTED,
            updated_at__gte=week_ago,
        ).count()

    return {
        "enabled": enabled,
        "environment": environment,
        "missing_creds": missing_creds,
        "recent_rejected": recent_rejected,
    }


def _check_low_stock() -> list[dict]:
    """List up to 10 active products with 0 < stock < 10 (warning band).

    Excludes ``stock=0`` — that's "out of stock", a different concern
    surfaced elsewhere. We only want the "almost out, reorder now" band.
    """

    Product = apps.get_model("product", "Product")
    rows = (
        Product.objects.filter(active=True, stock__gt=0, stock__lt=10)
        .order_by("stock", "id")
        .prefetch_related("translations")[:10]
    )
    out = []
    for product in rows:
        name = product.safe_translation_getter("name", any_language=True) or _(
            "Unnamed"
        )
        out.append(
            {
                "id": product.id,
                "name": name,
                "stock": product.stock,
                "url": reverse(
                    "admin:product_product_change", args=[product.id]
                ),
            }
        )
    return out


def _check_failed_celery() -> int:
    """Failed Celery tasks in the last 24h (best-effort).

    ``django_celery_results`` is optional. If the app is not installed
    we silently report zero so the Zone D banner just hides itself.
    """

    try:
        TaskResult = apps.get_model("django_celery_results", "TaskResult")
    except LookupError:
        return 0
    cutoff = timezone.now() - timedelta(hours=24)
    return TaskResult.objects.filter(
        status="FAILURE", date_done__gte=cutoff
    ).count()


# ── Zones A/B/C — cached payload ───────────────────────────────────────


def _build_zones_a_b_c() -> dict:
    User = apps.get_model("user", "UserAccount")
    Order = apps.get_model("order", "Order")
    ProductReview = apps.get_model("product", "ProductReview")
    Contact = apps.get_model("contact", "Contact")

    from order.enum.status import OrderStatus, PaymentStatus
    from product.enum.review import ReviewStatus

    now = timezone.now()
    week_ago = now - timedelta(days=7)
    prior_week_start = now - timedelta(days=14)
    month_ago = now - timedelta(days=30)
    today = now.date()

    return {
        **_zone_a_hero_kpis(
            Order,
            User,
            now,
            today,
            week_ago,
            prior_week_start,
            month_ago,
            PaymentStatus,
            OrderStatus,
        ),
        **_zone_b_ops_charts(Order, now, OrderStatus, PaymentStatus),
        **_zone_c_queues(Order, ProductReview, Contact, ReviewStatus),
    }


# ── Zone A — Hero KPIs ─────────────────────────────────────────────────


def _zone_a_hero_kpis(
    Order,
    User,
    now,
    today,
    week_ago,
    prior_week_start,
    month_ago,
    PaymentStatus,
    OrderStatus,
) -> dict:
    """4 hero KPI cards: revenue 7d, pending orders, new customers,
    average order value.
    """

    revenue_7d = (
        Order.objects.filter(
            payment_status=PaymentStatus.COMPLETED,
            created_at__gte=week_ago,
        ).aggregate(total=Sum("paid_amount"))["total"]
        or 0
    )
    revenue_prior = (
        Order.objects.filter(
            payment_status=PaymentStatus.COMPLETED,
            created_at__gte=prior_week_start,
            created_at__lt=week_ago,
        ).aggregate(total=Sum("paid_amount"))["total"]
        or 0
    )
    if revenue_prior:
        trend_pct = round(
            (float(revenue_7d) - float(revenue_prior))
            / float(revenue_prior)
            * 100,
            1,
        )
    else:
        trend_pct = None  # nothing to compare against

    pending_orders = Order.objects.filter(status=OrderStatus.PENDING).count()
    new_customers_30d = User.objects.filter(created_at__gte=month_ago).count()
    new_customers_today = User.objects.filter(created_at__date=today).count()
    aov_30d = (
        Order.objects.filter(created_at__gte=month_ago).aggregate(
            avg=Avg("paid_amount")
        )["avg"]
        or 0
    )

    return {
        "hero": {
            "revenue_7d": float(revenue_7d),
            "revenue_trend_pct": trend_pct,
            "pending_orders": pending_orders,
            "new_customers_30d": new_customers_30d,
            "new_customers_today": new_customers_today,
            "avg_order_value": round(float(aov_30d), 2),
        },
    }


# ── Zone B — Operations charts ────────────────────────────────────────


def _zone_b_ops_charts(Order, now, OrderStatus, PaymentStatus) -> dict:
    """Two charts: 14-day combined orders+revenue, status doughnut."""

    days = 14
    period_start = now - timedelta(days=days - 1)

    # ── Combined orders + revenue (last 14 days) ──
    orders_by_day = {
        item["day"].date(): item["count"]
        for item in Order.objects.filter(created_at__gte=period_start)
        .annotate(day=TruncDay("created_at"))
        .values("day")
        .annotate(count=Count("id"))
        if item["day"]
    }
    revenue_by_day = {
        item["day"].date(): float(item["total"] or 0)
        for item in Order.objects.filter(
            created_at__gte=period_start,
            payment_status=PaymentStatus.COMPLETED,
        )
        .annotate(day=TruncDay("created_at"))
        .values("day")
        .annotate(total=Sum("paid_amount"))
        if item["day"]
    }

    labels: list[str] = []
    orders_series: list[int] = []
    revenue_series: list[float] = []
    for i in range(days):
        day_val = (now - timedelta(days=days - 1 - i)).date()
        labels.append(day_val.strftime("%d/%m"))
        orders_series.append(orders_by_day.get(day_val, 0))
        revenue_series.append(revenue_by_day.get(day_val, 0))

    # Mixed bar/line per Chart.js v4 docs:
    # - dataset.type required on each (defaults don't merge for mixed)
    # - lower `order` = drawn last (on top); we want line ON TOP of bars
    # - borderSkipped="start" keeps the bottom of each bar flat on the
    #   x-axis baseline; only the top corners are rounded ("pill" look)
    # - solid saturated colors so bars carry the visual weight; line
    #   stays slim and subordinate so a single revenue spike doesn't
    #   visually overpower the daily orders bars
    performance_chart = {
        "labels": labels,
        "datasets": [
            {
                "label": str(_("Orders")),
                "type": "bar",
                "data": orders_series,
                "backgroundColor": "#6366f1",  # indigo-500
                "hoverBackgroundColor": "#4f46e5",  # indigo-600
                "borderRadius": 6,
                "borderSkipped": "start",
                "barPercentage": 0.85,
                "categoryPercentage": 0.9,
                "yAxisID": "y",
                "order": 2,
            },
            {
                "label": str(_("Revenue (€)")),
                "type": "line",
                "data": revenue_series,
                "borderColor": "#10b981",  # emerald-500
                "backgroundColor": "#10b981",
                "pointBackgroundColor": "#10b981",
                "pointBorderColor": "#ffffff",
                "pointBorderWidth": 2,
                "pointRadius": 4,
                "pointHoverRadius": 6,
                "borderWidth": 2.5,
                "fill": False,
                "tension": 0.3,
                "yAxisID": "y1",
                "order": 1,
            },
        ],
    }

    # ── Order status distribution doughnut ──
    status_palette = {
        OrderStatus.PENDING: "oklch(75% 0.15 85)",
        OrderStatus.PROCESSING: "oklch(65% 0.15 250)",
        OrderStatus.SHIPPED: "oklch(70% 0.12 200)",
        OrderStatus.DELIVERED: "oklch(70% 0.15 145)",
        OrderStatus.COMPLETED: "oklch(70% 0.15 145)",
        OrderStatus.CANCELED: "oklch(65% 0.2 25)",
        OrderStatus.RETURNED: "oklch(60% 0.15 30)",
        OrderStatus.REFUNDED: "oklch(60% 0.15 50)",
    }
    status_label_lookup = {
        OrderStatus.PENDING: _("Pending"),
        OrderStatus.PROCESSING: _("Processing"),
        OrderStatus.SHIPPED: _("Shipped"),
        OrderStatus.DELIVERED: _("Delivered"),
        OrderStatus.COMPLETED: _("Completed"),
        OrderStatus.CANCELED: _("Canceled"),
        OrderStatus.RETURNED: _("Returned"),
        OrderStatus.REFUNDED: _("Refunded"),
    }
    status_counts = (
        Order.objects.values("status")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    status_labels: list[str] = []
    status_data: list[int] = []
    status_colors: list[str] = []
    for row in status_counts:
        code = row["status"]
        status_labels.append(
            str(status_label_lookup.get(code, code.replace("_", " ").title()))
        )
        status_data.append(row["count"])
        status_colors.append(status_palette.get(code, "oklch(60% 0.05 250)"))

    status_chart = {
        "labels": status_labels,
        "datasets": [
            {
                "data": status_data,
                "backgroundColor": status_colors,
                "hoverOffset": 8,
                "borderWidth": 2,
                "borderColor": "#ffffff",
            }
        ],
    }

    return {
        "performance_chart": performance_chart,
        "status_chart": status_chart,
    }


# ── Zone C — Action queues ────────────────────────────────────────────


def _zone_c_queues(Order, ProductReview, Contact, ReviewStatus) -> dict:
    """Three compact action queues for staff to triage right from /admin/."""

    # Recent orders (top 6) — focus on action items, drop noise rows.
    # Don't use .only() with paid_amount: djmoney's MoneyField needs the
    # paired currency column loaded together or __set__ raises KeyError.
    recent_orders = Order.objects.select_related("user").order_by(
        "-created_at"
    )[:6]
    orders_table_rows = []
    for order in recent_orders:
        status_badge = _status_badge(order.status)
        # `paid_amount` is a djmoney Money instance — `.amount` is the
        # Decimal; `float(Money(...))` raises.
        paid = getattr(order.paid_amount, "amount", order.paid_amount) or 0
        amount = format_html(
            '<span class="font-semibold tabular-nums">€{}</span>',
            f"{float(paid):.2f}",
        )
        orders_table_rows.append(
            [
                format_html(
                    '<a href="{}" class="text-primary-600 dark:text-primary-400'
                    ' font-medium hover:underline">#{}</a>',
                    reverse("admin:order_order_change", args=[order.id]),
                    order.id,
                ),
                escape(order.email or "—"),
                status_badge,
                amount,
                order.created_at.strftime("%d/%m %H:%M"),
            ]
        )

    orders_table = {
        "headers": [
            _("Order #"),
            _("Customer"),
            _("Status"),
            _("Total"),
            _("Date"),
        ],
        "rows": orders_table_rows,
    }

    # Pending reviews — only NEW, max 5
    pending_reviews = (
        ProductReview.objects.filter(status=ReviewStatus.NEW)
        .select_related("product", "user")
        .prefetch_related("product__translations")
        .order_by("-created_at")[:5]
    )
    reviews_table_rows = []
    for review in pending_reviews:
        product_name = (
            review.product.safe_translation_getter("name", any_language=True)
            or "—"
        )
        reviews_table_rows.append(
            [
                format_html(
                    '<a href="{}" class="text-primary-600 dark:text-primary-400'
                    ' font-medium hover:underline">{}</a>',
                    reverse(
                        "admin:product_productreview_change", args=[review.id]
                    ),
                    escape(product_name[:30]),
                ),
                escape(
                    (review.user.email if review.user else _("Anonymous"))[:25]
                ),
                _rating_stars(review.rate),
                review.created_at.strftime("%d/%m"),
            ]
        )
    reviews_table = {
        "headers": [_("Product"), _("User"), _("Rating"), _("Date")],
        "rows": reviews_table_rows,
    }

    # Recent messages — last 5
    recent_messages = Contact.objects.order_by("-created_at")[:5]
    messages_table_rows = []
    for msg in recent_messages:
        messages_table_rows.append(
            [
                format_html(
                    '<a href="{}" class="text-primary-600 dark:text-primary-400'
                    ' font-medium hover:underline">{}</a>',
                    reverse("admin:contact_contact_change", args=[msg.id]),
                    escape(msg.name or msg.email or "—"),
                ),
                escape(msg.email or "—"),
                escape(
                    (msg.message or "")[:60]
                    + ("…" if len(msg.message or "") > 60 else "")
                ),
                msg.created_at.strftime("%d/%m"),
            ]
        )
    messages_table = {
        "headers": [_("From"), _("Email"), _("Message"), _("Date")],
        "rows": messages_table_rows,
    }

    return {
        "orders_table": orders_table,
        "reviews_table": reviews_table,
        "messages_table": messages_table,
    }


# ── Display helpers ────────────────────────────────────────────────────


def _status_badge(status: str):
    """Render a coloured pill for an order status code."""

    palette = {
        "PENDING": ("amber", _("Pending")),
        "PROCESSING": ("sky", _("Processing")),
        "SHIPPED": ("cyan", _("Shipped")),
        "DELIVERED": ("emerald", _("Delivered")),
        "COMPLETED": ("emerald", _("Completed")),
        "CANCELED": ("rose", _("Canceled")),
        "RETURNED": ("orange", _("Returned")),
        "REFUNDED": ("violet", _("Refunded")),
    }
    tone, label = palette.get(
        status, ("base", status.replace("_", " ").title())
    )
    return format_html(
        '<span class="inline-flex items-center rounded-full px-2 py-0.5 '
        "text-xs font-medium bg-{tone}-100 text-{tone}-700 "
        'dark:bg-{tone}-900/40 dark:text-{tone}-300">{label}</span>',
        tone=tone,
        label=label,
    )


def _rating_stars(rate) -> str:
    """Render the ``rate`` (1-10 in this project) as a compact pill."""

    rate = max(0, min(10, int(round(float(rate or 0)))))
    return format_html(
        '<span class="inline-flex items-center gap-1 rounded-full px-2 py-0.5'
        " text-xs font-medium bg-amber-100 text-amber-700"
        ' dark:bg-amber-900/40 dark:text-amber-300 tabular-nums">'
        "★ {rate}/10</span>",
        rate=rate,
    )
