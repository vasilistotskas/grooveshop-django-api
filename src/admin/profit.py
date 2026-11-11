"""The store's management profit report, its CSV and its XLSX export.

Custom pages of the store admin (``MyAdminSite.get_urls``), behind
``order.view_profit_report``: the store OWNER and platform superusers, not
ADMIN or STAFF (``tenant.role_scopes.OWNER_ONLY_PERMISSIONS``). Every figure
comes from ``order.profit``, which holds the definition; this module picks
the range, the bucket, the comparison and the breakdown, and lays the rows
out with the filters, columns and files the turnover page shares
(``admin.reports``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import BadRequest
from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView
from unfold.views import UnfoldSiteViewMixin

from admin.dashboard.base import bar_chart, change_percent, percent_text
from admin.displays import money
from admin.reports import (
    BREAKDOWN_PAGE_ROWS,
    COMPARE_YEAR,
    COUNT,
    MAX_PAGE_ROWS,
    MONEY,
    PERCENT,
    SHARE,
    Column,
    FilterForm,
    Kind,
    csv_response,
    display,
    filter_form,
    group_labels,
    period_key,
    period_label,
    right,
    xlsx_response,
    years,
)
from order.enum.expense import DELIVERY_EXPENSE_CATEGORIES, ExpenseCategory
from order.profit import (
    GroupProfit,
    Profit,
    figures,
    figures_by_group,
    figures_with_prior_year,
    total,
)
from order.turnover import Bucket, GroupBy, GroupKey, a_year_earlier

PERMISSION = "order.view_profit_report"

#: The share at which a coverage figure stops being a warning.
FULL_COVERAGE = Decimal(100)


# ── Columns: one definition for the page, the CSV and the XLSX ──────────

_EMPTY = Profit(period=None)


def _prior(row: Profit, prior: dict[date | None, Profit]) -> Profit:
    """The row a year earlier; the total row has no period and takes the
    prior total, which ``Report`` files under ``None``."""
    if row.period is None:
        return prior.get(None, _EMPTY)
    return prior.get(a_year_earlier(row.period), _EMPTY)


def _columns(
    *, compared: bool, prior: dict[date | None, Profit]
) -> list[Column]:
    columns = [
        Column(_("Net revenue"), lambda row: row.revenue, MONEY),
        Column(_("Cost of goods sold"), lambda row: row.cogs, MONEY),
        Column(_("Gross profit"), lambda row: row.gross_profit, MONEY),
        Column(_("Gross margin (%)"), lambda row: row.gross_margin, SHARE),
        Column(_("Payment fees"), lambda row: row.payment_fees, MONEY),
        Column(_("Delivery costs"), lambda row: row.delivery_costs, MONEY),
        Column(_("Contribution"), lambda row: row.contribution, MONEY),
        Column(_("Other expenses"), lambda row: row.other_expenses, MONEY),
        Column(_("Operating profit"), lambda row: row.operating_profit, MONEY),
        Column(_("Cost coverage (%)"), lambda row: row.cost_coverage, SHARE),
        Column(_("Fee coverage (%)"), lambda row: row.fee_coverage, SHARE),
    ]
    if compared:
        columns += [
            Column(
                _("Operating profit last year"),
                lambda row: _prior(row, prior).operating_profit,
                MONEY,
            ),
            Column(
                _("Change (%)"),
                lambda row: change_percent(
                    float(row.operating_profit),
                    float(_prior(row, prior).operating_profit),
                ),
                PERCENT,
            ),
        ]
    return columns


_GROUP_COLUMNS = [
    Column(_("Net goods"), lambda row: row.goods, MONEY),
    Column(_("Cost of goods sold"), lambda row: row.cogs, MONEY),
    Column(_("Gross profit"), lambda row: row.gross_profit, MONEY),
    Column(_("Gross margin (%)"), lambda row: row.gross_margin, SHARE),
    Column(_("Cost coverage (%)"), lambda row: row.cost_coverage, SHARE),
    Column(_("Units"), lambda row: row.units, COUNT),
]


# ── The report: what every view of it is built from ─────────────────────


@dataclass(frozen=True)
class Report:
    form: FilterForm
    bucket: Bucket
    rows: list[Profit]
    totals: Profit
    prior: dict[date | None, Profit]
    group_by: GroupBy | None
    groups: list[GroupProfit]
    labels: dict[GroupKey, str]

    @property
    def start(self) -> date:
        return self.form.cleaned_data["start"]

    @property
    def end(self) -> date:
        return self.form.cleaned_data["end"]

    @property
    def compared(self) -> bool:
        return self.form.compare_year

    @property
    def columns(self) -> list[Column]:
        return _columns(compared=self.compared, prior=self.prior)

    def period_name(self, row: Profit | GroupProfit) -> str:
        assert row.period is not None
        return period_label(row.period, self.bucket)


def build_report(form: FilterForm, *, group_rows: int | None) -> Report:
    """Run the report of a valid ``form``. ``group_rows`` caps the
    breakdown the page shows; ``None`` keeps every group."""
    start, end = form.cleaned_data["start"], form.cleaned_data["end"]
    bucket = Bucket(form.cleaned_data["bucket"])
    group_by = form.group_by

    prior: dict[date | None, Profit] = {}
    if form.compare_year:
        rows, earlier = figures_with_prior_year(start, end, bucket)
        prior = {period: row for period, row in earlier.items()}
        prior[None] = total(list(earlier.values()))
    else:
        rows = figures(start, end, bucket)

    groups: list[GroupProfit] = []
    labels: dict[GroupKey, str] = {}
    if group_by is not None:
        groups = figures_by_group(
            start, end, None if group_rows is not None else bucket, group_by
        )
        if group_rows is not None:
            groups = groups[:group_rows]
        labels = group_labels(group_by, {row.group for row in groups})

    return Report(
        form=form,
        bucket=bucket,
        rows=rows,
        totals=total(rows),
        prior=prior,
        group_by=group_by,
        groups=groups,
        labels=labels,
    )


# ── The page ────────────────────────────────────────────────────────────


def _table(report: Report) -> dict[str, Any]:
    columns = report.columns
    table_rows = [
        [report.period_name(row)]
        + [right(display(c.kind, c.value(row))) for c in columns]
        for row in report.rows
    ]
    table_rows.append(
        [gettext("Total")]
        + [right(display(c.kind, c.value(report.totals))) for c in columns]
    )
    return {
        "headers": [gettext("Period")]
        + [right(column.header) for column in columns],
        "rows": table_rows,
    }


def _group_table(report: Report) -> dict[str, Any]:
    assert report.group_by is not None
    return {
        "headers": [GroupBy(report.group_by).label]
        + [right(column.header) for column in _GROUP_COLUMNS],
        "rows": [
            [report.labels[row.group]]
            + [right(display(c.kind, c.value(row))) for c in _GROUP_COLUMNS]
            for row in report.groups
        ],
    }


def _expense_table(totals: Profit) -> dict[str, Any] | None:
    """The expenses of the range by category, delivery costs first."""
    amounts = {
        **{
            category: totals.delivery_costs
            for category in DELIVERY_EXPENSE_CATEGORIES
        },
        **totals.operating_costs,
    }
    rows = [
        [ExpenseCategory(category).label, right(money(amount))]
        for category, amount in sorted(
            amounts.items(), key=lambda item: -item[1]
        )
        if amount
    ]
    if not rows:
        return None
    return {
        "headers": [gettext("Category"), right(gettext("Net amount"))],
        "rows": rows,
    }


def _coverage_tone(share: Decimal | None) -> str:
    return "success" if share is None or share >= FULL_COVERAGE else "warning"


def _kpis(report: Report) -> list[dict[str, Any]]:
    totals = report.totals
    prior = report.prior.get(None, _EMPTY)
    change = change_percent(
        float(totals.operating_profit), float(prior.operating_profit)
    )
    profit_footer = (
        None
        if not report.compared
        else gettext("No profit in the same period last year")
        if change is None
        else gettext("%(change)s against the same period last year")
        % {"change": percent_text(change, signed=True)}
    )
    return [
        {
            "icon": "account_balance",
            "tone": "primary",
            "value": money(totals.revenue),
            "label": _("Net revenue"),
        },
        {
            "icon": "inventory_2",
            "tone": "warning",
            "value": money(totals.cogs),
            "label": _("Cost of goods sold"),
        },
        {
            "icon": "trending_up",
            "tone": "info",
            "value": money(totals.gross_profit),
            "label": _("Gross profit"),
        },
        {
            "icon": "local_shipping",
            "tone": "info",
            "value": money(totals.contribution),
            "label": _("Contribution"),
        },
        {
            "icon": "savings",
            "tone": "success" if totals.operating_profit >= 0 else "danger",
            "value": money(totals.operating_profit),
            "label": _("Operating profit"),
            "footer": profit_footer,
        },
        {
            "icon": "fact_check",
            "tone": _coverage_tone(totals.cost_coverage),
            "value": display(SHARE, totals.cost_coverage),
            "label": _("Cost coverage (%)"),
        },
        {
            "icon": "price_check",
            "tone": _coverage_tone(totals.fee_coverage),
            "value": display(SHARE, totals.fee_coverage),
            "label": _("Fee coverage (%)"),
        },
    ]


def _chart(report: Report) -> dict[str, str]:
    return bar_chart(
        [report.period_name(row) for row in report.rows],
        [float(row.operating_profit) for row in report.rows],
        label=gettext("Operating profit"),
        currency=True,
        prior=(
            gettext("Operating profit last year"),
            [
                float(_prior(row, report.prior).operating_profit)
                for row in report.rows
            ],
        )
        if report.compared
        else None,
    )


def _incomplete(totals: Profit) -> bool:
    """Is a coverage figure below 100%: is the profit an upper bound?"""
    return any(
        share is not None and share < FULL_COVERAGE
        for share in (totals.cost_coverage, totals.fee_coverage)
    )


class ProfitView(UnfoldSiteViewMixin, TemplateView):
    title = _("Profit")
    permission_required = (PERMISSION,)
    template_name = "admin/profit.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        form = filter_form(self.request, max_rows=MAX_PAGE_ROWS)
        index = reverse(f"{self.admin_site.name}:profit")
        today = timezone.localdate()
        context.update(
            {
                "form": form,
                "years": [
                    {
                        "label": str(year),
                        "href": f"{index}?"
                        + urlencode(
                            {
                                "start": date(year, 1, 1).isoformat(),
                                "end": min(
                                    date(year, 12, 31), today
                                ).isoformat(),
                                "bucket": Bucket.MONTH,
                                "compare": COMPARE_YEAR,
                            }
                        ),
                        "active": form.is_valid()
                        and form.cleaned_data["start"] == date(year, 1, 1)
                        and form.cleaned_data["end"]
                        == min(date(year, 12, 31), today),
                    }
                    for year in years()
                ],
            }
        )
        if not form.is_valid():
            return context
        report = build_report(form, group_rows=BREAKDOWN_PAGE_ROWS)
        query = urlencode(
            {
                name: form.cleaned_data[name] or ""
                for name in ("start", "end", "bucket", "compare", "group")
            }
        )
        context.update(
            {
                "report": report,
                "csv_url": f"{reverse(f'{self.admin_site.name}:profit-csv')}?{query}",
                "xlsx_url": f"{reverse(f'{self.admin_site.name}:profit-xlsx')}?{query}",
                "kpis": _kpis(report),
                "chart": _chart(report),
                "has_activity": any(
                    row.revenue or row.cogs or row.delivery_costs
                    for row in report.rows
                ),
                "incomplete": _incomplete(report.totals),
                "table": _table(report),
                "expense_table": _expense_table(report.totals),
                "group_table": _group_table(report)
                if report.group_by
                else None,
                "group_title": GroupBy(report.group_by).label
                if report.group_by
                else None,
            }
        )
        return context


# ── The exports ─────────────────────────────────────────────────────────


def _period_sheet(report: Report) -> list[list[Any]]:
    """Headings, a row per period, the total: raw values, typed by kind."""
    columns = report.columns
    headings = [gettext("Period")] + [str(c.header) for c in columns]
    body = [
        [period_key(row.period, report.bucket)]
        + [c.value(row) for c in columns]
        for row in report.rows
        if row.period is not None
    ]
    body.append([gettext("Total")] + [c.value(report.totals) for c in columns])
    return [headings, *body]


def _group_sheet(report: Report) -> list[list[Any]]:
    """The breakdown in long form: a row per period and group."""
    assert report.group_by is not None
    headings = [
        gettext("Period"),
        str(GroupBy(report.group_by).label),
        *(str(c.header) for c in _GROUP_COLUMNS),
    ]
    body = [
        [
            period_key(row.period, report.bucket) if row.period else "",
            report.labels[row.group],
            *(c.value(row) for c in _GROUP_COLUMNS),
        ]
        for row in report.groups
    ]
    return [headings, *body]


def _kinds(report: Report, *, group: bool) -> list[Kind]:
    columns = _GROUP_COLUMNS if group else report.columns
    return ["text"] * (2 if group else 1) + [c.kind for c in columns]


class ProfitExportView(UnfoldSiteViewMixin, TemplateView):
    """The report behind the page, over the same parameters, as a file
    (``admin.reports.csv_response``, ``xlsx_response``)."""

    title = _("Profit")
    permission_required = (PERMISSION,)
    filename_stem = "profit"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any):
        form = filter_form(request, max_rows=None)
        if not form.is_valid():
            raise BadRequest("; ".join(form.errors.keys()))
        report = build_report(form, group_rows=None)
        return self.render_report(report)

    def render_report(self, report: Report) -> HttpResponse:
        raise NotImplementedError

    def filename(self, report: Report, extension: str) -> str:
        return f"{self.filename_stem}-{report.start}-{report.end}.{extension}"


class ProfitCsvView(ProfitExportView):
    """One table: the periods, or the breakdown when one was asked for."""

    def render_report(self, report: Report) -> HttpResponse:
        group = report.group_by is not None
        return csv_response(
            _group_sheet(report) if group else _period_sheet(report),
            _kinds(report, group=group),
            self.filename(report, "csv"),
        )


class ProfitXlsxView(ProfitExportView):
    """The periods, and the breakdown on a second sheet when asked for."""

    def render_report(self, report: Report) -> HttpResponse:
        sheets = [
            (
                gettext("Profit"),
                _period_sheet(report),
                _kinds(report, group=False),
            )
        ]
        if report.group_by is not None:
            sheets.append(
                (
                    str(GroupBy(report.group_by).label),
                    _group_sheet(report),
                    _kinds(report, group=True),
                )
            )
        return xlsx_response(sheets, self.filename(report, "xlsx"))
