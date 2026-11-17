from datetime import timedelta

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.db.models.functions import TruncDay
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.translation import gettext_lazy as _
from unfold.datasets import BaseDataset

from admin.datasets import RelatedDatasetAdmin
from admin.displays import (
    change_link,
    format_dt,
)
from order.models.order import Order
from product.admin.variants import STOCK_STATUS_VARIANT
from product.models.product import (
    Product,
)


class StockLogDatasetAdmin(RelatedDatasetAdmin):
    parent_field = "product"
    list_display = (
        "operation_type",
        "quantity_change",
        "stock_levels",
        "order_link",
        "performed_by_display",
        "created_at",
    )
    ordering = ("-created_at",)
    staff_actor_fields = ("performed_by_staff",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("performed_by")

    @admin.display(description=_("Change"))
    def quantity_change(self, obj):
        return f"{obj.quantity_delta:+d}"

    @admin.display(description=_("Stock Level"))
    def stock_levels(self, obj):
        return f"{obj.stock_before} → {obj.stock_after}"

    @admin.display(description=_("Related Order"))
    def order_link(self, obj):
        if obj.order_id:
            return change_link(
                self.admin_site,
                Order,
                obj.order_id,
                _("Order #%(id)s") % {"id": obj.order_id},
            )
        return obj.reason_display or "—"

    @admin.display(description=_("By"))
    def performed_by_display(self, obj):
        actor = obj.actor
        if actor:
            return getattr(actor, "email", "") or getattr(actor, "username", "")
        return _("System")


class StockLogDataset(BaseDataset):
    """Every stock movement of the product, paged (the inline this
    replaces claimed "recent 20" and rendered them all)."""

    from order.models.stock_log import StockLog

    model = StockLog
    model_admin = StockLogDatasetAdmin
    title = _("Stock activity")
    tab = True


class StockHistoryAdminMixin:
    """The product's stock-history report page (``stock_history_view``)."""

    def stock_history_view(self, request, product_id):
        """A product's stock movements: a daily chart and the latest entries.

        Built from the dashboard's chart and table helpers
        (``admin.dashboard.base``) so it follows the theme; Unfold's
        header builds the breadcrumbs from ``opts`` and ``original``.
        """
        from admin.dashboard.base import bar_chart_options, chart_json, color
        from admin.displays import label_cell, link_cell
        from order.models.stock_log import StockLog

        product = get_object_or_404(Product, pk=product_id)
        if not self.has_view_permission(request, product):
            raise PermissionDenied

        windows = (7, 30, 90, 180, 365)
        try:
            window_days = int(request.GET.get("days") or 90)
        except TypeError, ValueError:
            window_days = 90
        if window_days not in windows:
            window_days = 90
        today = timezone.localdate()
        days = [
            today - timedelta(days=n) for n in range(window_days - 1, -1, -1)
        ]

        totals: dict[tuple, int] = {
            (row["day"].date(), row["operation_type"]): int(row["total"] or 0)
            for row in StockLog.objects.filter(
                product_id=product.pk, created_at__date__gte=days[0]
            )
            .annotate(day=TruncDay("created_at"))
            .values("day", "operation_type")
            .annotate(total=Sum("quantity_delta"))
        }
        operation_colors = {
            StockLog.OPERATION_RESERVE: color("orange-400"),
            StockLog.OPERATION_RELEASE: color("primary-400"),
            StockLog.OPERATION_DECREMENT: color("red-500"),
            StockLog.OPERATION_INCREMENT: color("green-500"),
        }
        operation_variants = {
            StockLog.OPERATION_RESERVE: "warning",
            StockLog.OPERATION_RELEASE: "primary",
            StockLog.OPERATION_DECREMENT: "danger",
            StockLog.OPERATION_INCREMENT: "success",
        }
        chart = {
            "labels": [formats.date_format(day, "d/m") for day in days],
            "datasets": [
                {
                    "label": str(label),
                    "type": "bar",
                    "data": [totals.get((day, code), 0) for day in days],
                    "backgroundColor": operation_colors[code],
                    "stack": "stock",
                    "borderRadius": 4,
                    "maxBarThickness": 24,
                }
                for code, label in StockLog.OPERATION_TYPE_CHOICES
            ],
        }

        logs = (
            StockLog.objects.filter(product_id=product.pk)
            .select_related("performed_by")
            .prefetch_related("performed_by_staff")
            .order_by("-created_at")[:50]
        )
        rows = [
            [
                format_dt(log.created_at),
                label_cell(
                    log.get_operation_type_display(),
                    operation_variants[log.operation_type],
                ),
                {"content": f"{log.quantity_delta:+d}", "class": "text-right"},
                {"content": log.stock_before, "class": "text-right"},
                {"content": log.stock_after, "class": "text-right"},
                log.reason_display or "—",
                link_cell(
                    reverse("admin:order_order_change", args=[log.order_id]),
                    f"#{log.order_id}",
                )
                if log.order_id
                else "—",
                log.actor or _("System"),
            ]
            for log in logs
        ]

        stock_state = product.stock_state

        context = {
            **self.admin_site.each_context(request),
            "title": _("Stock history"),
            "opts": self.model._meta,
            "original": product,
            "product": product,
            "stock_state": stock_state,
            "stock_tone": STOCK_STATUS_VARIANT[stock_state],
            "window_days": window_days,
            "windows": windows,
            "has_activity": bool(totals),
            "chart_data": chart_json(chart),
            "chart_options": chart_json(
                bar_chart_options(legend=True, stacked=True)
            ),
            "table": {
                "headers": [
                    _("When"),
                    _("Operation"),
                    {"content": _("Change"), "class": "text-right"},
                    {"content": _("Before"), "class": "text-right"},
                    {"content": _("After"), "class": "text-right"},
                    _("Reason"),
                    _("Order"),
                    _("By"),
                ],
                "rows": rows,
            },
        }
        return render(request, "admin/product/stock_history.html", context)
