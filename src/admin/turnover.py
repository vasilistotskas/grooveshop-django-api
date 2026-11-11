"""The store's turnover report, its CSV and its XLSX export.

Custom pages of the store admin (``MyAdminSite.get_urls``), behind
``order.view_turnover_report``: store ADMIN and OWNER and platform
superusers, not STAFF (``tenant.auth_backends``). Every figure comes from
``order.turnover``, which holds the definition; this module picks the
range, the bucket, the comparison and the breakdown, and lays the rows
out. The page, the CSV and the XLSX read one list of columns, so they can
not disagree about what a column is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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

from admin.dashboard.base import (
    bar_chart,
    change_percent,
    percent_text,
)
from admin.displays import money
from admin.reports import (
    BREAKDOWN_PAGE_ROWS,
    COMPARE_YEAR,
    COUNT,
    MAX_PAGE_ROWS,
    MONEY,
    PERCENT,
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
from order.turnover import (
    Bucket,
    GroupBy,
    GroupKey,
    Row,
    a_year_earlier,
    figures,
    figures_with_prior_year,
    total,
)

PERMISSION = "order.view_turnover_report"


# ── Labels ──────────────────────────────────────────────────────────────


# ── Columns: one definition for the page, the CSV and the XLSX ──────────


def _columns(*, compared: bool, prior: dict[date | None, Row]) -> list[Column]:
    columns = [
        Column(_("Goods"), lambda row: row.goods, MONEY),
        Column(_("Shipping"), lambda row: row.shipping, MONEY),
        Column(_("Fees"), lambda row: row.fees, MONEY),
        Column(_("Returns"), lambda row: row.returns_net, MONEY),
        Column(_("Net turnover"), lambda row: row.turnover, MONEY),
        Column(_("VAT"), lambda row: row.net_vat, MONEY),
        Column(_("Gross"), lambda row: row.gross, MONEY),
        Column(_("Orders"), lambda row: row.orders, COUNT),
        Column(_("Units"), lambda row: row.units, COUNT),
        Column(_("Average order"), lambda row: row.average_order_value, MONEY),
        Column(
            _("Awaiting collection"),
            lambda row: row.awaiting_collection,
            MONEY,
        ),
    ]
    if compared:
        columns += [
            Column(
                _("Net turnover last year"),
                lambda row: _prior(row, prior).turnover,
                MONEY,
            ),
            Column(
                _("Change (%)"),
                lambda row: change_percent(
                    float(row.turnover), float(_prior(row, prior).turnover)
                ),
                PERCENT,
            ),
        ]
    return columns


_GROUP_COLUMNS = [
    Column(_("Net sales"), lambda row: row.sales_net, MONEY),
    Column(_("VAT"), lambda row: row.vat, MONEY),
    Column(_("Gross"), lambda row: row.sales_gross, MONEY),
    Column(_("Orders"), lambda row: row.orders, COUNT),
    Column(_("Units"), lambda row: row.units, COUNT),
]

_EMPTY = Row(period=None, group=None)


def _prior(row: Row, prior: dict[date | None, Row]) -> Row:
    """The row a year earlier; the total row has no period and takes the
    prior total, which ``Report`` files under ``None``."""
    if row.period is None:
        return prior.get(None, _EMPTY)
    return prior.get(a_year_earlier(row.period), _EMPTY)


# ── The report: what every view of it is built from ─────────────────────


@dataclass(frozen=True)
class Report:
    form: FilterForm
    bucket: Bucket
    rows: list[Row]
    totals: Row
    prior: dict[date | None, Row]
    group_by: GroupBy | None
    groups: list[Row]
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

    def period_name(self, row: Row) -> str:
        return period_label(row.period, self.bucket)


def build_report(form: FilterForm, *, group_rows: int | None) -> Report:
    """Run the report of a valid ``form``. ``group_rows`` caps the
    breakdown the page shows; ``None`` keeps every group."""
    start, end = form.cleaned_data["start"], form.cleaned_data["end"]
    bucket = Bucket(form.cleaned_data["bucket"])
    group_by = form.group_by

    prior: dict[date | None, Row] = {}
    if form.compare_year:
        rows, by_period = figures_with_prior_year(start, end, bucket)
        prior = {**by_period, None: total(list(by_period.values()))}
    else:
        rows = figures(start, end, bucket)

    groups: list[Row] = []
    labels: dict[GroupKey, str] = {}
    if group_by is not None:
        groups = figures(
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


def _kpis(report: Report) -> list[dict[str, Any]]:
    totals = report.totals
    prior = report.prior.get(None, _EMPTY)
    change = change_percent(float(totals.turnover), float(prior.turnover))
    turnover_footer = (
        None
        if not report.compared
        else gettext("No sales in the same period last year")
        if change is None
        else gettext("%(change)s against the same period last year")
        % {"change": percent_text(change, signed=True)}
    )
    return [
        {
            "icon": "account_balance",
            "tone": "success",
            "value": money(totals.turnover),
            "label": _("Net turnover"),
            "footer": turnover_footer,
        },
        {
            "icon": "assignment_return",
            "tone": "warning",
            "value": money(totals.returns_net),
            "label": _("Returns"),
        },
        {
            "icon": "percent",
            "tone": "info",
            "value": money(totals.net_vat),
            "label": _("VAT"),
        },
        {
            "icon": "payments",
            "tone": "primary",
            "value": money(totals.gross),
            "label": _("Gross"),
        },
        {
            "icon": "receipt_long",
            "tone": "info",
            "value": totals.orders,
            "label": _("Orders"),
        },
        {
            "icon": "shopping_bag",
            "tone": "primary",
            "value": money(totals.average_order_value),
            "label": _("Average order"),
        },
        {
            "icon": "inventory_2",
            "tone": "info",
            "value": totals.units,
            "label": _("Units sold"),
        },
        {
            "icon": "local_shipping",
            "tone": "warning",
            "value": money(totals.awaiting_collection),
            "label": _("Awaiting collection"),
        },
    ]


def _chart(report: Report) -> dict[str, str]:
    return bar_chart(
        [report.period_name(row) for row in report.rows],
        [float(row.turnover) for row in report.rows],
        label=gettext("Net turnover"),
        currency=True,
        prior=(
            gettext("Net turnover last year"),
            [float(_prior(row, report.prior).turnover) for row in report.rows],
        )
        if report.compared
        else None,
    )


class TurnoverView(UnfoldSiteViewMixin, TemplateView):
    title = _("Turnover")
    permission_required = (PERMISSION,)
    template_name = "admin/turnover.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        form = filter_form(self.request, max_rows=MAX_PAGE_ROWS)
        index = reverse(f"{self.admin_site.name}:turnover")
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
                "csv_url": f"{reverse(f'{self.admin_site.name}:turnover-csv')}?{query}",
                "xlsx_url": f"{reverse(f'{self.admin_site.name}:turnover-xlsx')}?{query}",
                "kpis": _kpis(report),
                "chart": _chart(report),
                "has_sales": any(row.orders for row in report.rows),
                "table": _table(report),
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
            period_key(row.period, report.bucket),
            report.labels[row.group],
            *(c.value(row) for c in _GROUP_COLUMNS),
        ]
        for row in report.groups
    ]
    return [headings, *body]


def _kinds(report: Report, *, group: bool) -> list[Kind]:
    columns = _GROUP_COLUMNS if group else report.columns
    return ["text"] * (2 if group else 1) + [c.kind for c in columns]


class TurnoverExportView(UnfoldSiteViewMixin, TemplateView):
    """The report behind the page, over the same parameters, as a file.

    Amounts are plain numbers with a point, periods ISO (``2026-10``): a
    file for a spreadsheet or an accountant's import, not for reading, so
    nothing in it follows the viewer's locale but the headings.
    """

    title = _("Turnover")
    permission_required = (PERMISSION,)
    filename_stem = "turnover"

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


class TurnoverCsvView(TurnoverExportView):
    """One table: the periods, or the breakdown when one was asked for."""

    def render_report(self, report: Report) -> HttpResponse:
        group = report.group_by is not None
        return csv_response(
            _group_sheet(report) if group else _period_sheet(report),
            _kinds(report, group=group),
            self.filename(report, "csv"),
        )


class TurnoverXlsxView(TurnoverExportView):
    """The periods, and the breakdown on a second sheet when asked for."""

    def render_report(self, report: Report) -> HttpResponse:
        sheets = [
            (
                gettext("Turnover"),
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
