"""The store's monthly turnover report and its CSV export.

Two custom pages of the store admin (``MyAdminSite.get_urls``), behind
``order.view_turnover_report``: store ADMIN and OWNER and platform
superusers, not STAFF (``tenant.auth_backends``). Every figure comes from
``order.turnover``, which holds the definition; this module only picks the
year and lays the months out.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from typing import Any

from django.core.exceptions import BadRequest
from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView
from unfold.views import UnfoldSiteViewMixin

from admin.displays import money
from order.turnover import MonthTurnover, first_year, turnover_by_month

PERMISSION = "order.view_turnover_report"

# A spreadsheet opens a UTF-8 file without a byte-order mark as Latin-1,
# which turns the Greek headings into noise.
BYTE_ORDER_MARK = "﻿"


def _years() -> list[int]:
    """The years offered, newest first: from the first sale to this one."""
    current = timezone.localdate().year
    first = first_year() or current
    return list(range(current, first - 1, -1))


def _requested_year(request: HttpRequest, years: list[int]) -> int:
    """``?year=``; this year when absent, a 400 when it is not offered."""
    raw = request.GET.get("year")
    if raw is None:
        return years[0]
    try:
        year = int(raw)
    except ValueError:
        raise BadRequest("year must be a number") from None
    if year not in years:
        raise BadRequest("year has no sales")
    return year


def _totals(months: list[MonthTurnover]) -> tuple[Decimal, Decimal, int]:
    return (
        sum((m.sales for m in months), Decimal(0)),
        sum((m.returns for m in months), Decimal(0)),
        sum(m.orders for m in months),
    )


class TurnoverView(UnfoldSiteViewMixin, TemplateView):
    title = _("Turnover")
    permission_required = (PERMISSION,)
    template_name = "admin/turnover.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        years = _years()
        year = _requested_year(self.request, years)
        months = turnover_by_month(year)
        sales, returns, orders = _totals(months)
        index = reverse(f"{self.admin_site.name}:turnover")
        context.update(
            {
                "year": year,
                "years": [
                    {
                        "label": str(candidate),
                        "href": f"{index}?year={candidate}",
                        "active": candidate == year,
                    }
                    for candidate in years
                ],
                "export_url": f"{reverse(f'{self.admin_site.name}:turnover-csv')}"
                f"?year={year}",
                "kpis": [
                    {
                        "icon": "shopping_bag",
                        "tone": "primary",
                        "value": money(sales),
                        "label": _("Net sales"),
                    },
                    {
                        "icon": "assignment_return",
                        "tone": "warning",
                        "value": money(returns),
                        "label": _("Returns"),
                    },
                    {
                        "icon": "account_balance",
                        "tone": "success",
                        "value": money(sales - returns),
                        "label": _("Net turnover"),
                    },
                    {
                        "icon": "receipt_long",
                        "tone": "info",
                        "value": orders,
                        "label": _("Orders"),
                    },
                ],
                "table": _table(months, sales, returns, orders),
            }
        )
        return context


def _table(
    months: list[MonthTurnover], sales: Decimal, returns: Decimal, orders: int
) -> dict[str, Any]:
    return {
        "headers": [
            _("Month"),
            _("Net sales"),
            _("Returns"),
            _("Net turnover"),
            _("Orders"),
        ],
        "rows": [
            [
                date_format(m.month, "F"),
                money(m.sales),
                money(m.returns),
                money(m.net),
                m.orders,
            ]
            for m in months
        ]
        + [
            [
                gettext("Total"),
                money(sales),
                money(returns),
                money(sales - returns),
                orders,
            ]
        ],
    }


class TurnoverCsvView(UnfoldSiteViewMixin, TemplateView):
    """The same twelve months as the page, one row each and a total.

    Amounts are plain decimals with a point (``1234.50``), months ISO
    (``2026-10``): a file for a spreadsheet or an accountant's import, not
    for reading, so nothing in it follows the viewer's locale but the
    headings.
    """

    title = _("Turnover")
    permission_required = (PERMISSION,)

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        year = _requested_year(request, _years())
        months = turnover_by_month(year)
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="turnover-{year}.csv"'
        )
        response.write(BYTE_ORDER_MARK)
        writer = csv.writer(response)
        writer.writerow(
            [
                gettext("Month"),
                gettext("Net sales"),
                gettext("Returns"),
                gettext("Net turnover"),
                gettext("Orders"),
            ]
        )
        for m in months:
            writer.writerow(
                [
                    f"{m.month:%Y-%m}",
                    f"{m.sales:.2f}",
                    f"{m.returns:.2f}",
                    f"{m.net:.2f}",
                    m.orders,
                ]
            )
        sales, returns, orders = _totals(months)
        writer.writerow(
            [
                gettext("Total"),
                f"{sales:.2f}",
                f"{returns:.2f}",
                f"{sales - returns:.2f}",
                orders,
            ]
        )
        return response
