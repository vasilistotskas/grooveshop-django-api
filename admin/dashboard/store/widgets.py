"""The store dashboard's widgets.

Each class is an Unfold component rendered by a partial under
``core/templates/admin/dashboard/``; the layout is
``core/templates/admin/index.html``. Cached data comes from
``admin.dashboard.store.queries``; everything below runs per request, in
the viewer's language.

Alerts are the exception to caching by design: a merchant who fixes a
missing setting must see the banner go on the next load.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.apps import apps
from django.utils import formats, timezone
from django.utils.text import Truncator
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from unfold.components import register_component

from admin.dashboard.base import (
    DashboardWidget,
    bar_chart_options,
    chart_json,
    color,
    doughnut_chart,
    percent,
    percent_text,
)
from admin.dashboard.store import queries
from admin.displays import ORDER_STATUS_VARIANT, label_cell, link_cell, money
from order.enum.status import OrderStatus

REVENUE_PERIOD_LABELS = {
    "7d": (_("7d"), _("Last 7 days")),
    "30d": (_("1m"), _("Last 30 days")),
    "90d": (_("3m"), _("Last 3 months")),
    "365d": (_("1y"), _("Last 12 months")),
}

# One colour per order status, from the scales Unfold defines. Distinct
# shades rather than ORDER_STATUS_VARIANT, which gives processing and
# shipped (and delivered and completed) the same colour.
ORDER_STATUS_COLOR = {
    OrderStatus.PENDING: color("orange-400"),
    OrderStatus.PROCESSING: color("blue-400"),
    OrderStatus.SHIPPED: color("primary-400"),
    OrderStatus.DELIVERED: color("green-400"),
    OrderStatus.COMPLETED: color("green-600"),
    OrderStatus.CANCELED: color("red-500"),
    OrderStatus.RETURNED: color("orange-600"),
    OrderStatus.REFUNDED: color("base-400"),
}

ORDER_SOURCE_COLORS = (
    color("primary-500"),
    color("green-500"),
    color("orange-400"),
    color("blue-400"),
    color("red-400"),
    color("primary-300"),
)
ORDER_SOURCE_OTHER_COLOR = color("base-400")


def _day_label(day) -> str:
    return formats.date_format(day, "d/m")


# ── KPI cards ───────────────────────────────────────────────────────────


@register_component
class StoreRevenueCard(DashboardWidget):
    """Revenue for a selectable window; every window ships in the page
    and Alpine switches between them (``$persist`` keeps the choice)."""

    query = queries.kpis

    def shape(self, data: dict[str, Any]) -> dict[str, Any]:
        periods = []
        for key, _days in queries.REVENUE_PERIODS:
            short_label, long_label = REVENUE_PERIOD_LABELS[key]
            figure = data["revenue"][key]
            trend = figure["trend_pct"]
            periods.append(
                {
                    "key": key,
                    "short_label": short_label,
                    "long_label": long_label,
                    "amount": money(figure["amount"]),
                    "trend": None
                    if trend is None
                    else {
                        "text": percent_text(trend, signed=True),
                        "variant": "success" if trend >= 0 else "danger",
                        "icon": "trending_up"
                        if trend >= 0
                        else "trending_down",
                    },
                }
            )
        return {"periods": periods, "default_period": periods[0]["key"]}


@register_component
class StorePendingOrdersCard(DashboardWidget):
    query = queries.kpis

    def shape(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "icon": "pending_actions",
            "tone": "warning",
            "value": data["pending_orders"],
            "label": _("Pending orders"),
            "href": self.changelist_url("order.Order", "status__exact=PENDING"),
            "footer": _("Open to triage"),
        }


@register_component
class StoreNewCustomersCard(DashboardWidget):
    query = queries.kpis

    def shape(self, data: dict[str, Any]) -> dict[str, Any]:
        today = data["new_customers_today"]
        return {
            "icon": "person_add",
            "tone": "info",
            "value": data["new_customers_30d"],
            "label": _("New customers"),
            "badge": (
                {
                    "text": _("+%(count)s today") % {"count": today},
                    "variant": "info",
                }
                if today
                else None
            ),
            "href": self.changelist_url("user.UserAccount"),
            "footer": _("Last 30 days"),
        }


@register_component
class StoreAverageOrderValueCard(DashboardWidget):
    query = queries.kpis

    def shape(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "icon": "receipt_long",
            "tone": "primary",
            "value": money(data["avg_order_value"]),
            "label": _("Average order value"),
            "footer": _("Paid orders, last 30 days"),
        }


# ── Charts ──────────────────────────────────────────────────────────────


@register_component
class StorePerformanceChart(DashboardWidget):
    """Orders per day as bars, paid revenue as a line on a € axis."""

    query = queries.performance

    def shape(self, data: list[dict[str, Any]]) -> dict[str, Any]:
        has_data = any(day["orders"] or day["revenue"] for day in data)
        chart = {
            "labels": [_day_label(day["day"]) for day in data],
            "datasets": [
                {
                    "label": str(_("Orders")),
                    "type": "bar",
                    "data": [day["orders"] for day in data],
                    "backgroundColor": color("primary-500"),
                    "borderRadius": 6,
                    "borderSkipped": "start",
                    "maxBarThickness": 28,
                    "yAxisID": "y",
                    "order": 2,
                },
                {
                    "label": str(_("Revenue")),
                    "type": "line",
                    "data": [day["revenue"] for day in data],
                    "borderColor": color("green-500"),
                    "backgroundColor": color("green-500"),
                    "borderWidth": 2,
                    "pointRadius": 3,
                    # Monotone: a smoothed line never overshoots below zero
                    # between two days.
                    "cubicInterpolationMode": "monotone",
                    "yAxisID": "y1",
                    "order": 1,
                },
            ],
        }
        return {
            "title": _("Revenue and orders"),
            "description": _("Last 14 days"),
            "empty_text": _("No orders in the last 14 days"),
            "class": "lg:col-span-2",
            "has_data": has_data,
            "data": chart_json(chart),
            "options": chart_json(
                bar_chart_options(legend=True, currency_axis=True)
            ),
        }


@register_component
class StoreOrderStatusChart(DashboardWidget):
    query = queries.order_status

    def shape(self, data: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "title": _("Order status"),
            "description": _("All orders"),
            "empty_text": _("No orders yet"),
            "chart": doughnut_chart(
                [str(OrderStatus(row["status"]).label) for row in data],
                [row["count"] for row in data],
                [ORDER_STATUS_COLOR[row["status"]] for row in data],
            )
            if data
            else None,
        }


@register_component
class StoreOrderSourceChart(DashboardWidget):
    query = queries.order_source

    def shape(self, data: dict[str, Any]) -> dict[str, Any]:
        from order.attribution import source_label

        labels = [
            str(source_label(row["source"], row["source_type"]))
            for row in data["top"]
        ]
        values = [row["count"] for row in data["top"]]
        colors = list(ORDER_SOURCE_COLORS[: len(values)])
        if data["other"]:
            labels.append(str(_("Other")))
            values.append(data["other"])
            colors.append(ORDER_SOURCE_OTHER_COLOR)
        return {
            "title": _("Orders by source"),
            "description": _("Last 30 days"),
            "empty_text": _(
                "No attributed orders in the last 30 days. Orders are "
                "attributed from the visit that placed them."
            ),
            "chart": doughnut_chart(labels, values, colors) if values else None,
        }


# ── Queues ──────────────────────────────────────────────────────────────


@register_component
class StoreRecentOrders(DashboardWidget):
    query = queries.recent_orders

    def shape(self, data: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "title": _("Recent orders"),
            "action_href": self.changelist_url("order.Order"),
            "empty_icon": "shopping_bag",
            "empty_text": _("No orders yet"),
            "table": {
                "headers": [
                    _("Order"),
                    _("Customer"),
                    _("Status"),
                    {"content": _("Total"), "class": "text-right"},
                    _("Placed"),
                ],
                "rows": [
                    [
                        link_cell(
                            self.change_url("order.Order", row["id"]),
                            f"#{row['id']}",
                        ),
                        row["email"] or "—",
                        label_cell(
                            OrderStatus(row["status"]).label,
                            ORDER_STATUS_VARIANT[row["status"]],
                        ),
                        {
                            "content": money(row["amount"]),
                            "class": "text-right",
                        },
                        formats.date_format(
                            timezone.localtime(row["created_at"]), "d/m H:i"
                        ),
                    ]
                    for row in data
                ],
            },
        }


@register_component
class StorePendingReviews(DashboardWidget):
    query = queries.pending_reviews

    def shape(self, data: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "title": _("Reviews awaiting approval"),
            "action_href": self.changelist_url(
                "product.ProductReview", "status__exact=NEW"
            ),
            "empty_icon": "task_alt",
            "empty_text": _("No reviews are waiting"),
            "table": {
                "headers": [
                    _("Product"),
                    _("Customer"),
                    _("Rating"),
                    _("Received"),
                ],
                "rows": [
                    [
                        link_cell(
                            self.change_url("product.ProductReview", row["id"]),
                            Truncator(row["product"] or "—").chars(40),
                        ),
                        row["user_email"] or _("Anonymous"),
                        label_cell(f"★ {row['rate']}/10", "warning"),
                        formats.date_format(
                            timezone.localtime(row["created_at"]), "d/m"
                        ),
                    ]
                    for row in data
                ],
            },
        }


@register_component
class StoreContactMessages(DashboardWidget):
    query = queries.contact_messages

    def shape(self, data: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "title": _("Contact messages"),
            "action_href": self.changelist_url("contact.Contact"),
            "empty_icon": "mail",
            "empty_text": _("No messages yet"),
            "table": {
                "headers": [_("From"), _("Message"), _("Received")],
                "rows": [
                    [
                        link_cell(
                            self.change_url("contact.Contact", row["id"]),
                            row["name"] or row["email"],
                        ),
                        Truncator(row["message"]).chars(60),
                        formats.date_format(
                            timezone.localtime(row["created_at"]), "d/m"
                        ),
                    ]
                    for row in data
                ],
            },
        }


# ── Growth ──────────────────────────────────────────────────────────────


@register_component
class StoreFunnel(DashboardWidget):
    """Carts started → orders → paid, each bar relative to carts started."""

    query = queries.funnel

    def shape(self, data: dict[str, int]) -> dict[str, Any]:
        started = data["started"]

        def step(title, count):
            # A whole number: Unfold writes it into a CSS width, and a
            # localised float ("62,5") is not valid CSS.
            return {
                "title": title,
                "description": count,
                "value": round(percent(count, started)),
            }

        return {
            "has_data": bool(started),
            "steps": [
                step(_("Carts started"), started),
                step(_("Orders placed"), data["orders"]),
                step(_("Paid"), data["paid"]),
            ],
            "overall": percent_text(percent(data["paid"], started)),
        }


@register_component
class StoreRepeatCustomers(DashboardWidget):
    query = queries.retention

    def shape(self, data: dict[str, int]) -> dict[str, Any]:
        return {
            "icon": "autorenew",
            "tone": "success",
            "value": percent_text(percent(data["repeat"], data["paying"])),
            "label": _("Repeat customers"),
            "footer": _(
                "%(repeat)s of %(paying)s paying customers ordered again"
            )
            % {"repeat": data["repeat"], "paying": data["paying"]},
        }


@register_component
class StoreTopProducts(DashboardWidget):
    query = queries.top_products

    def shape(self, data: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "title": _("Most viewed products"),
            "action_href": self.changelist_url("product.Product"),
            "empty_icon": "inventory_2",
            "empty_text": _("No products yet"),
            "table": {
                "headers": [
                    _("Product"),
                    {"content": _("Views"), "class": "text-right"},
                ],
                "rows": [
                    [
                        link_cell(
                            self.change_url("product.Product", row["id"]),
                            Truncator(row["name"] or "—").chars(48),
                        ),
                        {
                            "content": formats.number_format(
                                row["views"], use_l10n=True, force_grouping=True
                            ),
                            "class": "text-right",
                        },
                    ]
                    for row in data
                ],
            },
        }


# ── Search ──────────────────────────────────────────────────────────────


@register_component
class StoreSearchKpis(DashboardWidget):
    query = queries.search_insights

    def shape(self, data: dict[str, Any]) -> dict[str, Any]:
        clicks = data["clicks"]
        return {
            "kpis": [
                {
                    "icon": "search",
                    "tone": "primary",
                    "value": formats.number_format(
                        data["searches"], use_l10n=True, force_grouping=True
                    ),
                    "label": _("Searches"),
                    "footer": _("Last 30 days, empty searches excluded"),
                },
                {
                    "icon": "search_off",
                    "tone": "warning",
                    "value": percent_text(
                        percent(data["zero"], data["meaningful"])
                    ),
                    "label": _("Found nothing"),
                    "footer": ngettext(
                        "%(count)s search, last 30 days",
                        "%(count)s searches, last 30 days",
                        data["zero"],
                    )
                    % {"count": data["zero"]},
                },
                {
                    "icon": "ads_click",
                    "tone": "success",
                    # "—" until click tracking has recorded anything: a
                    # zero nobody measured would read as "nobody clicks".
                    "value": "—" if clicks is None else clicks,
                    "label": _("Result clicks"),
                    "footer": (
                        _("No clicks recorded yet")
                        if clicks is None
                        else _("%(rate)s of searches, last 30 days")
                        % {
                            "rate": percent_text(
                                percent(clicks, data["searches"])
                            )
                        }
                    ),
                },
            ]
        }


@register_component
class StoreSearchVolume(DashboardWidget):
    query = queries.search_insights

    def shape(self, data: dict[str, Any]) -> dict[str, Any]:
        volume = data["volume"]
        chart = {
            "labels": [_day_label(day["day"]) for day in volume],
            "datasets": [
                {
                    "label": str(_("Searches")),
                    "type": "bar",
                    "data": [day["count"] for day in volume],
                    "backgroundColor": color("blue-400"),
                    "borderRadius": 6,
                    "borderSkipped": "start",
                    "maxBarThickness": 28,
                }
            ],
        }
        return {
            "title": _("Search volume"),
            "description": _("Last 14 days, empty searches excluded"),
            "empty_text": _("No searches in the last 14 days"),
            "class": "lg:col-span-2",
            "has_data": any(day["count"] for day in volume),
            "data": chart_json(chart),
            "options": chart_json(
                bar_chart_options(legend=False, currency_axis=False)
            ),
        }


def _query_table(rows: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    return {
        "headers": [
            _("Query"),
            {"content": _("Searches"), "class": "text-right"},
        ],
        "rows": [
            [
                row["query"],
                {
                    "content": label_cell(row["count"], variant),
                    "class": "text-right",
                },
            ]
            for row in rows
        ],
    }


@register_component
class StoreTopQueries(DashboardWidget):
    query = queries.search_insights

    def shape(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": _("Top searches"),
            "description": _("Last 30 days"),
            "empty_icon": "search",
            "empty_text": _("No searches yet"),
            "table": _query_table(data["top_queries"], "primary"),
        }


@register_component
class StoreZeroResultQueries(DashboardWidget):
    """What visitors looked for and did not find: the synonym, content
    and catalogue worklist."""

    query = queries.search_insights

    def shape(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": _("Searches that found nothing"),
            "description": _("Candidates for synonyms, content or products"),
            "empty_icon": "task_alt",
            "empty_text": _("Every search found something"),
            "table": _query_table(data["zero_queries"], "warning"),
        }


# ── Per-request: stock and alerts ───────────────────────────────────────


@register_component
class StoreLowStock(DashboardWidget):
    """Active products about to run out (superusers). Fresh per request:
    it is the list a merchant works through while restocking."""

    def shape(self, data: None) -> dict[str, Any]:
        if not self.is_superuser:
            return {"visible": False}
        Product = apps.get_model("product", "Product")
        products = (
            Product.objects.filter(
                active=True, stock__gt=0, stock__lt=queries.LOW_STOCK_BELOW
            )
            .order_by("stock", "id")
            .prefetch_related("translations")[:10]
        )
        rows = [
            [
                link_cell(
                    self.change_url("product.Product", product.id),
                    Truncator(
                        product.safe_translation_getter(
                            "name", any_language=True
                        )
                        or "—"
                    ).chars(48),
                ),
                {
                    "content": label_cell(product.stock, "danger"),
                    "class": "text-right",
                },
            ]
            for product in products
        ]
        return {
            "visible": bool(rows),
            "title": _("Running low"),
            "description": _("Active products with fewer than %(count)s left")
            % {"count": queries.LOW_STOCK_BELOW},
            "action_href": self.changelist_url(
                "product.Product", f"stock__lt={queries.LOW_STOCK_BELOW}"
            ),
            "table": {
                "headers": [
                    _("Product"),
                    {"content": _("In stock"), "class": "text-right"},
                ],
                "rows": rows,
            },
        }


def _alert(
    variant: str,
    icon: str,
    title: Any,
    text: Any = "",
    items: list[Any] | None = None,
    link: tuple[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "variant": variant,
        "icon": icon,
        "title": title,
        "text": text,
        "items": items or [],
        "link": {"href": link[0], "text": link[1]} if link else None,
    }


_REQUIRED_SELLER_SETTINGS = (
    ("INVOICE_SELLER_NAME", _("Company name")),
    ("INVOICE_SELLER_VAT_ID", _("VAT ID (ΑΦΜ)")),
    ("INVOICE_SELLER_TAX_OFFICE", _("Tax office (ΔΟΥ)")),
)


@register_component
class StoreAlerts(DashboardWidget):
    """What needs someone's attention, checked on every load.

    Legal-page updates go to whoever may edit content pages (the
    merchant). Invoicing and myDATA configuration go to superusers.
    """

    def shape(self, data: None) -> dict[str, Any]:
        alerts = self._legal_updates()
        if self.is_superuser:
            alerts += self._seller_config() + self._mydata()
        return {"alerts": alerts}

    def _legal_updates(self) -> list[dict[str, Any]]:
        if not self.request.user.has_perm("page_config.change_contentpage"):
            return []
        from page_config.defaults import pending_legal_reviews

        pages = {page.pk: page for page, _update in pending_legal_reviews()}
        if not pages:
            return []
        return [
            _alert(
                "warning",
                "gavel",
                _("Your legal pages need an update"),
                _(
                    "The platform has added new text to its legal documents. "
                    "These pages were not changed automatically because their "
                    "text is yours. Open each one to see the text to add."
                ),
                items=[
                    link_cell(
                        self.change_url("page_config.ContentPage", pk), page
                    )
                    for pk, page in pages.items()
                ],
            )
        ]

    def _seller_config(self) -> list[dict[str, Any]]:
        from extra_settings.models import Setting

        missing = [
            label
            for key, label in _REQUIRED_SELLER_SETTINGS
            if not Setting.get(key, default="")
        ]
        if not missing:
            return []
        return [
            _alert(
                "danger",
                "warning",
                _("Invoice seller details are incomplete"),
                _(
                    "Invoices issued now will render without a legal identity "
                    "and will not pass a tax audit."
                ),
                items=missing,
                link=(
                    self.changelist_url(
                        "extra_settings.Setting", "q=INVOICE_SELLER_"
                    ),
                    _("Fix in settings"),
                ),
            )
        ]

    def _mydata(self) -> list[dict[str, Any]]:
        from extra_settings.models import Setting

        from order.models.invoice import Invoice, MyDataStatus

        if not Setting.get("MYDATA_ENABLED", default=False):
            return []
        alerts = []
        missing = [
            key
            for key in ("MYDATA_USER_ID", "MYDATA_SUBSCRIPTION_KEY")
            if not Setting.get(key, default="")
        ]
        if missing:
            alerts.append(
                _alert(
                    "danger",
                    "cloud_off",
                    _("myDATA is enabled but its credentials are missing"),
                    _("Every submission to AADE will fail until they are set."),
                    items=missing,
                    link=(
                        self.changelist_url(
                            "extra_settings.Setting", "q=MYDATA_"
                        ),
                        _("Fix in settings"),
                    ),
                )
            )
        rejected = Invoice.objects.filter(
            mydata_status=MyDataStatus.REJECTED,
            updated_at__gte=timezone.now() - timedelta(days=7),
        ).count()
        if rejected:
            alerts.append(
                _alert(
                    "warning",
                    "report",
                    ngettext(
                        "%(count)s invoice rejected by myDATA in the last 7 days",
                        "%(count)s invoices rejected by myDATA in the last 7 days",
                        rejected,
                    )
                    % {"count": rejected},
                    link=(
                        self.changelist_url(
                            "order.Invoice", "mydata_status__exact=REJECTED"
                        ),
                        _("Review rejected invoices"),
                    ),
                )
            )
        if (
            not missing
            and Setting.get("MYDATA_ENVIRONMENT", default="dev") == "dev"
        ):
            alerts.append(
                _alert(
                    "info",
                    "science",
                    _("myDATA is using the AADE sandbox"),
                    _("Set MYDATA_ENVIRONMENT to 'prod' when you are ready."),
                )
            )
        return alerts
