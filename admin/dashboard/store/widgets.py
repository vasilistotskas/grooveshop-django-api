"""The store dashboard's widgets.

Each class is an Unfold component rendered by a partial under
``core/templates/admin/dashboard/``; the layout is
``core/templates/admin/index.html``. Cached data comes from
``admin.dashboard.store.queries``; everything below runs per request, in
the viewer's language.

The page shows one period at a time, picked by ``?period=`` (links, not
script: the address can be shared and the back button works), and the
sales chart one measure, picked by ``?metric=``. Optional features follow
``admin.features``: a widget for a feature the store does not have, or
has switched off, is not drawn; a link is drawn only for someone who may
open its page.

Alerts are the exception to caching by design: a merchant who fixes a
missing setting must see the banner go on the next load.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

from django.urls import reverse
from django.utils import formats, timezone
from django.utils.text import Truncator
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext, pgettext_lazy
from unfold.components import register_component

from admin.dashboard.base import (
    DashboardWidget,
    bar_chart,
    change_percent,
    percent,
    percent_text,
    sparkline,
)
from admin.dashboard.store import queries
from admin.dashboard.store.queries import PERIODS, PERIODS_BY_KEY, Period
from admin.displays import ORDER_STATUS_VARIANT, label_cell, link_cell, money
from admin.features import store_features
from admin.navigation import changelist_link
from order.enum.status import OrderStatus, PaymentStatus

PERIOD_LABELS = {
    "7d": (_("7 days"), _("Last 7 days")),
    "30d": (_("30 days"), _("Last 30 days")),
    "13w": (_("13 weeks"), _("Last 13 weeks")),
    "12m": (_("12 months"), _("Last 12 months")),
}

METRICS = {
    "revenue": (_("Revenue"), _("Paid revenue per %(bucket)s")),
    "orders": (_("Orders"), _("Orders placed per %(bucket)s")),
}
DEFAULT_METRIC = "revenue"

BUCKET_NAMES = {
    "day": pgettext_lazy("chart bucket", "day"),
    "week": pgettext_lazy("chart bucket", "week"),
    "month": pgettext_lazy("chart bucket", "month"),
}


def selected_period(request: Any) -> Period:
    return PERIODS_BY_KEY.get(
        request.GET.get("period", ""), PERIODS_BY_KEY[queries.DEFAULT_PERIOD]
    )


def selected_metric(request: Any) -> str:
    metric = request.GET.get("metric", "")
    return metric if metric in METRICS else DEFAULT_METRIC


def _dashboard_href(request: Any, **changes: str) -> str:
    """The dashboard with ``changes`` applied to its period and metric."""
    params = {
        "period": selected_period(request).key,
        "metric": selected_metric(request),
        **changes,
    }
    return f"?{urlencode(params)}"


def _bucket_label(start, bucket: str) -> str:
    if bucket == "month":
        return formats.date_format(start, "M")
    return formats.date_format(start, "d/m")


def _days_ago(days: int) -> str:
    """The cutoff a rolling count used, for the list its row opens."""
    return (timezone.now() - timedelta(days=days)).isoformat()


def _number(value: float) -> str:
    return formats.number_format(value, use_l10n=True, force_grouping=True)


def _trend(current: float, prior: float) -> dict[str, str] | None:
    change = change_percent(current, prior)
    if change is None:
        return None
    rising = change >= 0
    return {
        "text": percent_text(change, signed=True),
        "variant": "success" if rising else "danger",
        "icon": "trending_up" if rising else "trending_down",
    }


def _kpi_card(
    label: Any,
    value: str,
    trend: dict[str, str] | None,
    line: list[float],
    footer: Any,
    href: str | None,
) -> dict[str, Any]:
    return {
        "label": label,
        "value": value,
        "trend": trend,
        "spark": sparkline(line) if any(line) else None,
        "footer": footer,
        "href": href,
    }


class StorePeriodWidget(DashboardWidget):
    """A widget whose query is asked for the period on display."""

    def variant(self) -> str | None:
        return selected_period(self.request).key

    @property
    def period_label(self) -> Any:
        return PERIOD_LABELS[selected_period(self.request).key][1]


# ── Header ──────────────────────────────────────────────────────────────


@register_component
class StoreDashboardHeader(DashboardWidget):
    """Greeting, date, the period switch and the one action every
    store takes most: adding a product."""

    def shape(self, data: None) -> dict[str, Any]:
        user = self.request.user
        name = (getattr(user, "first_name", "") or "").strip()
        hour = timezone.localtime().hour
        if not name:
            title = _("Dashboard")
        elif hour < 12:
            title = _("Good morning, %(name)s") % {"name": name}
        elif hour < 18:
            title = _("Good afternoon, %(name)s") % {"name": name}
        else:
            title = _("Good evening, %(name)s") % {"name": name}
        period = selected_period(self.request)
        return {
            "title": title,
            "date": formats.date_format(timezone.localdate(), "l, j E"),
            "period_label": PERIOD_LABELS[period.key][1],
            "periods": [
                {
                    "label": PERIOD_LABELS[option.key][0],
                    "href": _dashboard_href(self.request, period=option.key),
                    "active": option.key == period.key,
                }
                for option in PERIODS
            ],
            "new_product_href": (
                reverse("admin:product_product_add")
                if user.has_perm("product.add_product")
                else None
            ),
        }


# ── Headline figures ────────────────────────────────────────────────────


@register_component
class StoreKpis(StorePeriodWidget):
    """Revenue, orders, average order and new customers for the period,
    each against the period before it, with its trend over the buckets."""

    query = queries.overview

    def shape(self, data: dict[str, Any]) -> dict[str, Any]:
        series = queries.sales_series.get(self.variant())
        compared = _("vs the period before")
        # An average of no orders is not zero: "—", and nothing to compare.
        average = data["revenue"] / data["paid"] if data["paid"] else None
        prior_average = (
            data["prior_revenue"] / data["prior_paid"]
            if data["prior_paid"]
            else 0.0
        )
        if data["paying_customers"]:
            customers_footer = _(
                "%(percent)s of paying customers ordered again"
            ) % {
                "percent": percent_text(
                    percent(data["repeat_customers"], data["paying_customers"])
                )
            }
        else:
            customers_footer = compared
        return {
            "cards": [
                _kpi_card(
                    _("Revenue"),
                    money(data["revenue"]),
                    _trend(data["revenue"], data["prior_revenue"]),
                    [row["revenue"] for row in series],
                    compared,
                    changelist_link(
                        self.request,
                        "order.Order",
                        f"payment_status__exact={PaymentStatus.COMPLETED}",
                    ),
                ),
                _kpi_card(
                    _("Orders"),
                    _number(data["orders"]),
                    _trend(data["orders"], data["prior_orders"]),
                    [row["orders"] for row in series],
                    compared,
                    changelist_link(self.request, "order.Order"),
                ),
                _kpi_card(
                    _("Average order"),
                    "—" if average is None else money(average),
                    None if average is None else _trend(average, prior_average),
                    [
                        row["revenue"] / row["paid"] if row["paid"] else 0.0
                        for row in series
                    ],
                    _("Paid orders"),
                    None,
                ),
                _kpi_card(
                    _("New customers"),
                    _number(data["new_customers"]),
                    _trend(data["new_customers"], data["prior_new_customers"]),
                    [row["customers"] for row in series],
                    customers_footer,
                    changelist_link(self.request, "user.UserAccount"),
                ),
            ]
        }


# ── Sales chart ─────────────────────────────────────────────────────────


@register_component
class StoreSalesChart(StorePeriodWidget):
    """One measure per bucket of the period, revenue or orders."""

    query = queries.sales_series

    def shape(self, data: list[dict[str, Any]]) -> dict[str, Any]:
        period = selected_period(self.request)
        metric = selected_metric(self.request)
        title, description = METRICS[metric]
        values = [row[metric] for row in data]
        return {
            "title": title,
            "description": _("%(measure)s · %(period)s")
            % {
                "measure": description
                % {"bucket": BUCKET_NAMES[period.bucket]},
                "period": self.period_label,
            },
            "metrics": [
                {
                    "label": METRICS[key][0],
                    "href": _dashboard_href(self.request, metric=key),
                    "active": key == metric,
                }
                for key in METRICS
            ],
            "has_data": any(values),
            "empty_text": _("No orders in this period"),
            "chart": bar_chart(
                [_bucket_label(row["start"], period.bucket) for row in data],
                values,
                label=str(title),
                currency=metric == "revenue",
            ),
        }


# ── Work waiting ────────────────────────────────────────────────────────


@register_component
class StoreAttention(DashboardWidget):
    """Everything waiting for someone, as counts that open the filtered
    list. A row needs its feature, a count, and a page the viewer may
    open; the same counts drive the sidebar badges."""

    query = queries.attention_counts

    def shape(self, data: dict[str, int]) -> dict[str, Any]:
        from order.models.invoice import MyDataStatus

        features = store_features(self.request)
        candidates = [
            (
                True,
                "pending_orders",
                "receipt_long",
                "primary",
                _("Orders to process"),
                _("Placed and waiting"),
                "order.Order",
                f"status__exact={OrderStatus.PENDING}",
            ),
            (
                True,
                "low_stock",
                "inventory_2",
                "warning",
                _("Running low on stock"),
                _("Active products with fewer than %(count)s left")
                % {"count": queries.LOW_STOCK_BELOW},
                "product.Product",
                f"active__exact=1&stock__gt=0&stock__lt={queries.LOW_STOCK_BELOW}",
            ),
            (
                features.mydata,
                "mydata_rejected",
                "report",
                "danger",
                _("Receipts rejected by myDATA"),
                _("In the last %(days)s days")
                % {"days": queries.MYDATA_REJECTED_DAYS},
                "order.Invoice",
                urlencode(
                    {
                        "mydata_status__exact": MyDataStatus.REJECTED,
                        "updated_at__gte": _days_ago(
                            queries.MYDATA_REJECTED_DAYS
                        ),
                    }
                ),
            ),
            (
                features.reviews,
                "pending_reviews",
                "star_rate",
                "default",
                _("Reviews to approve"),
                _("Hidden from shoppers until approved"),
                "product.ProductReview",
                "status__exact=NEW",
            ),
            (
                features.blog_comments,
                "pending_comments",
                "chat_bubble",
                "default",
                _("Blog comments to approve"),
                _("Hidden from readers until approved"),
                "blog.BlogComment",
                "approved__exact=0",
            ),
            (
                True,
                "new_contact_messages",
                "mail",
                "default",
                _("New messages"),
                _("In the last %(days)s days")
                % {"days": queries.NEW_MESSAGES_DAYS},
                "contact.Contact",
                urlencode(
                    {"created_at__gte": _days_ago(queries.NEW_MESSAGES_DAYS)}
                ),
            ),
            (
                features.wholesale.live,
                "pending_business_profiles",
                "business_center",
                "default",
                _("Business accounts to verify"),
                _("Waiting for wholesale prices"),
                "b2b.BusinessProfile",
                "status__exact=PENDING",
            ),
        ]
        rows = []
        for (
            shown,
            key,
            icon,
            variant,
            label,
            detail,
            model,
            query,
        ) in candidates:
            count = data[key]
            if not shown or not count:
                continue
            href = changelist_link(self.request, model, query)
            if href is None:
                continue
            rows.append(
                {
                    "icon": icon,
                    "variant": variant,
                    "label": label,
                    "detail": detail,
                    "count": _number(count),
                    "href": href,
                }
            )
        return {"rows": rows}


# ── Orders and products ─────────────────────────────────────────────────


@register_component
class StoreRecentOrders(DashboardWidget):
    query = queries.recent_orders

    def shape(self, data: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "title": _("Latest orders"),
            "action_href": changelist_link(self.request, "order.Order"),
            "empty_icon": "shopping_bag",
            "empty_text": _("No orders yet"),
            "table": {
                "headers": [
                    _("Order"),
                    _("Customer"),
                    _("Placed"),
                    _("Status"),
                    {"content": _("Total"), "class": "text-right"},
                ],
                "rows": [
                    [
                        link_cell(
                            self.change_url("order.Order", row["id"]),
                            f"#{row['id']}",
                        ),
                        row["email"] or _("Guest"),
                        formats.date_format(
                            timezone.localtime(row["created_at"]), "d/m H:i"
                        ),
                        label_cell(
                            OrderStatus(row["status"]).label,
                            ORDER_STATUS_VARIANT[row["status"]],
                        ),
                        {
                            "content": money(row["amount"]),
                            "class": "text-right",
                        },
                    ]
                    for row in data
                ],
            },
        }


@register_component
class StoreBestSellers(StorePeriodWidget):
    query = queries.best_sellers

    def shape(self, data: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "title": _("Best sellers"),
            "description": _("Units paid for · %(period)s")
            % {"period": self.period_label},
            "action_href": changelist_link(self.request, "product.Product"),
            "empty_icon": "inventory_2",
            "empty_text": _("No paid orders in this period"),
            "table": {
                "headers": [
                    _("Product"),
                    {"content": _("Sold"), "class": "text-right"},
                ],
                "rows": [
                    [
                        link_cell(
                            self.change_url("product.Product", row["id"]),
                            Truncator(row["name"] or "—").chars(48),
                        ),
                        {
                            "content": _number(row["units"]),
                            "class": "text-right",
                        },
                    ]
                    for row in data
                ],
            },
        }


# ── Growth ──────────────────────────────────────────────────────────────


@register_component
class StoreFunnel(StorePeriodWidget):
    """Carts started → orders → paid, each bar relative to carts started."""

    query = queries.funnel

    def shape(self, data: dict[str, int]) -> dict[str, Any]:
        started = data["started"]

        def step(title, count):
            # A whole number: Unfold writes it into a CSS width, and a
            # localised float ("62,5") is not valid CSS.
            return {
                "title": title,
                "description": _number(count),
                "value": round(percent(count, started)),
            }

        return {
            "description": self.period_label,
            "has_data": bool(started),
            "steps": [
                step(_("Carts started"), started),
                step(_("Orders placed"), data["orders"]),
                step(_("Paid"), data["paid"]),
            ],
            "overall": percent_text(percent(data["paid"], started)),
        }


@register_component
class StoreOrderSources(StorePeriodWidget):
    """Where the period's attributed orders came from, as shares."""

    query = queries.order_sources

    def shape(self, data: dict[str, Any]) -> dict[str, Any]:
        from order.attribution import source_label

        total = data["total"]
        rows = [
            (str(source_label(row["source"], row["source_type"])), row["count"])
            for row in data["top"]
        ]
        if data["other"]:
            rows.append((str(_("Other")), data["other"]))
        return {
            "description": self.period_label,
            "rows": [
                {
                    "title": title,
                    "description": ngettext(
                        "%(count)s order · %(share)s",
                        "%(count)s orders · %(share)s",
                        count,
                    )
                    % {
                        "count": _number(count),
                        "share": percent_text(percent(count, total)),
                    },
                    "value": round(percent(count, total)),
                }
                for title, count in rows
            ],
            "empty_text": _(
                "No attributed orders in this period. Orders are attributed "
                "from the visit that placed them."
            ),
        }


@register_component
class StoreSearch(StorePeriodWidget):
    """Store search in one card: how much, how often nothing was found,
    and the two lists to act on."""

    query = queries.search

    def shape(self, data: dict[str, Any]) -> dict[str, Any]:
        clicks = data["clicks"]
        return {
            "description": self.period_label,
            "stats": [
                {"value": _number(data["searches"]), "label": _("Searches")},
                {
                    "value": percent_text(
                        percent(data["zero"], data["meaningful"])
                    ),
                    "label": _("Found nothing"),
                },
                {
                    # "—" until click tracking has recorded anything: a
                    # zero nobody measured would read as "nobody clicks".
                    "value": "—"
                    if clicks is None
                    else percent_text(percent(clicks, data["searches"])),
                    "label": _("Clicked a result"),
                },
            ],
            "lists": [
                {
                    "key": "zero",
                    "label": _("Found nothing"),
                    "hint": _("Add synonyms, products or content for these."),
                    "variant": "warning",
                    "rows": data["zero_queries"],
                    "empty_text": _("Every search found something"),
                },
                {
                    "key": "top",
                    "label": _("Top searches"),
                    "hint": _("What shoppers look for most."),
                    "variant": "default",
                    "rows": data["top_queries"],
                    "empty_text": _("No searches in this period"),
                },
            ],
        }


# ── Per-request: alerts ─────────────────────────────────────────────────


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


# Owner-facing name of each field of the merchant's legal identity
# (``tenant.legal_identity``), which decides what is required and when.
_IDENTITY_FIELD_LABELS = {
    "name": pgettext_lazy("legal details alert", "Company name"),
    "legal_form": pgettext_lazy(
        "legal details alert", "Legal form (e.g. sole trader, IKE, AE)"
    ),
    "registration_number": pgettext_lazy("legal details alert", "GEMI number"),
    "vat_id": pgettext_lazy("legal details alert", "VAT ID (ΑΦΜ)"),
    "tax_office": pgettext_lazy("legal details alert", "Tax office (ΔΟΥ)"),
    "address_line_1": pgettext_lazy(
        "legal details alert", "Address (street and number)"
    ),
    "city": pgettext_lazy("legal details alert", "City"),
    "postal_code": pgettext_lazy("legal details alert", "Postal code"),
    "country": pgettext_lazy("legal details alert", "Country"),
    "email": pgettext_lazy("legal details alert", "Contact email"),
}


@register_component
class StoreAlerts(DashboardWidget):
    """What needs someone's attention, checked on every load.

    Legal-page updates go to whoever may edit content pages (the
    merchant). The legal-identity alerts go to whoever may change store
    settings (superusers, and the merchant's owner and admins - not
    staff), since that is where the fix is made. myDATA configuration
    goes to superusers.
    """

    def shape(self, data: None) -> dict[str, Any]:
        alerts = self._legal_updates()
        if self.request.user.has_perm("extra_settings.change_setting"):
            alerts += self._legal_identity()
        if self.is_superuser:
            alerts += self._mydata()
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

    def _settings_link(self, text: Any) -> tuple[str, Any]:
        return (
            self.changelist_url("extra_settings.Setting", "q=INVOICE_SELLER_"),
            text,
        )

    def _legal_identity(self) -> list[dict[str, Any]]:
        from extra_settings.models import Setting

        from tenant.legal_identity import (
            merchant_legal_identity,
            missing_disclosure_fields,
            missing_tax_document_fields,
        )

        identity = merchant_legal_identity()
        alerts = []
        # A compliance gap, not an outage: calm, and always shown.
        if missing := missing_disclosure_fields(identity):
            alerts.append(
                _alert(
                    "warning",
                    "policy",
                    _("Your business's legal details are missing"),
                    _(
                        "The law requires your online store to show your "
                        "business details. Please fill in what is missing:"
                    ),
                    items=[_IDENTITY_FIELD_LABELS[f] for f in missing],
                    link=self._settings_link(_("Fill in details")),
                )
            )
        # Only a store that issues tax documents needs these, and for it
        # a gap is an outage: every submission to AADE fails.
        if Setting.get("MYDATA_ENABLED", default=False) and (
            missing := missing_tax_document_fields(identity)
        ):
            alerts.append(
                _alert(
                    "danger",
                    "receipt_long",
                    _("Details needed to issue receipts are missing"),
                    _(
                        "myDATA is on, but receipts cannot be sent to AADE "
                        "without the following:"
                    ),
                    items=[_IDENTITY_FIELD_LABELS[f] for f in missing],
                    link=self._settings_link(_("Fill in details")),
                )
            )
        return alerts

    def _mydata(self) -> list[dict[str, Any]]:
        from extra_settings.models import Setting

        from tenant.credentials import mydata_credentials

        if not Setting.get("MYDATA_ENABLED", default=False):
            return []
        alerts = []
        credentials = mydata_credentials()
        missing = [
            field
            for field, key in (
                ("mydata_user_id", "user_id"),
                ("mydata_subscription_key", "subscription_key"),
            )
            if not credentials[key]
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
                        self.changelist_url("tenant.Tenant"),
                        _("Fix in settings"),
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
