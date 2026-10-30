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

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from django import forms
from django.core.exceptions import BadRequest
from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.http import urlencode
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from unfold.views import UnfoldSiteViewMixin
from unfold.widgets import (
    INPUT_CLASSES,
    UnfoldAdminSelectWidget,
)

from admin.dashboard.base import (
    bar_chart,
    change_percent,
    percent_text,
)
from admin.displays import money
from order.enum.attribution import OrderSourceType
from order.turnover import (
    Bucket,
    GroupBy,
    GroupKey,
    Row,
    a_year_earlier,
    buckets,
    figures,
    figures_with_prior_year,
    first_year,
    total,
)
from pay_way.enum.pay_way import PayWayEnum

PERMISSION = "order.view_turnover_report"

# A spreadsheet opens a UTF-8 file without a byte-order mark as Latin-1,
# which turns the Greek headings into noise.
BYTE_ORDER_MARK = "﻿"

#: The most rows the page draws. A table of thousands of days is not a
#: report anyone reads; the export has no such limit.
MAX_PAGE_ROWS = 400

#: The biggest groups a breakdown shows on the page; the export lists all.
BREAKDOWN_PAGE_ROWS = 25

COMPARE_YEAR = "year"

#: Excel refuses a sheet name longer than this.
EXCEL_TITLE_LENGTH = 31
XLSX_MONEY_FORMAT = "#,##0.00"

XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


class FilterForm(forms.Form):
    """What the page shows: validated, so the page, the CSV and the XLSX
    refuse the same requests."""

    start = forms.DateField(
        label=_("From"),
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={"type": "date", "class": " ".join(INPUT_CLASSES)},
        ),
    )
    end = forms.DateField(
        label=_("To"),
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={"type": "date", "class": " ".join(INPUT_CLASSES)},
        ),
    )
    bucket = forms.ChoiceField(
        label=_("Group by period"),
        choices=Bucket.choices,
        widget=UnfoldAdminSelectWidget,
    )
    compare = forms.ChoiceField(
        label=_("Compare with"),
        choices=[
            ("", _("Nothing")),
            (COMPARE_YEAR, _("Same period last year")),
        ],
        required=False,
        widget=UnfoldAdminSelectWidget,
    )
    group = forms.ChoiceField(
        label=_("Break down by"),
        choices=[("", _("Nothing")), *GroupBy.choices],
        required=False,
        widget=UnfoldAdminSelectWidget,
    )

    def __init__(
        self, data: dict[str, str], *, max_rows: int | None = None
    ) -> None:
        super().__init__(data)
        self.max_rows = max_rows

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end:
            if end < start:
                self.add_error("end", _("The end is before the start."))
            elif (
                self.max_rows is not None
                and cleaned.get("bucket")
                and sum(1 for _day in buckets(start, end, cleaned["bucket"]))
                > self.max_rows
            ):
                self.add_error(
                    "bucket",
                    _(
                        "This range would show more than %(rows)s rows: "
                        "choose longer periods, or export it."
                    )
                    % {"rows": self.max_rows},
                )
        return cleaned

    @property
    def compare_year(self) -> bool:
        return self.cleaned_data["compare"] == COMPARE_YEAR

    @property
    def group_by(self) -> GroupBy | None:
        return (
            GroupBy(self.cleaned_data["group"])
            if self.cleaned_data["group"]
            else None
        )


def _defaults() -> dict[str, str]:
    today = timezone.localdate()
    return {
        "start": date(today.year, 1, 1).isoformat(),
        "end": today.isoformat(),
        "bucket": Bucket.MONTH,
        "compare": COMPARE_YEAR,
        "group": "",
    }


def _filter_form(request: HttpRequest, *, max_rows: int | None) -> FilterForm:
    """The request over the defaults: a parameter that is absent takes its
    default, one that is present (a ``compare`` left empty) is kept."""
    return FilterForm({**_defaults(), **request.GET.dict()}, max_rows=max_rows)


# ── Labels ──────────────────────────────────────────────────────────────


def period_label(period: date, bucket: Bucket) -> str:
    """A period as the page prints it, in the active language."""
    match bucket:
        case Bucket.DAY:
            return date_format(period, "SHORT_DATE_FORMAT")
        case Bucket.WEEK:
            return gettext("Week of %(day)s") % {
                "day": date_format(period, "SHORT_DATE_FORMAT")
            }
        case Bucket.MONTH:
            return date_format(period, "YEAR_MONTH_FORMAT")
        case Bucket.QUARTER:
            return gettext("Q%(quarter)s %(year)s") % {
                "quarter": (period.month - 1) // 3 + 1,
                "year": period.year,
            }


def period_key(period: date, bucket: Bucket) -> str:
    """A period as a file carries it: ISO, whatever the language."""
    match bucket:
        case Bucket.DAY:
            return period.isoformat()
        case Bucket.WEEK:
            year, week, _day = period.isocalendar()
            return f"{year}-W{week:02d}"
        case Bucket.MONTH:
            return f"{period:%Y-%m}"
        case Bucket.QUARTER:
            return f"{period.year}-Q{(period.month - 1) // 3 + 1}"


def group_labels(group_by: GroupBy, keys: set[GroupKey]) -> dict[GroupKey, str]:
    """The names of the groups a breakdown found."""
    from product.models import Product, ProductCategory

    match group_by:
        case GroupBy.PAYMENT_METHOD:
            return {
                key: PayWayEnum(key).label if key in PayWayEnum else "—"
                for key in keys
            }
        case GroupBy.CHANNEL:
            return {
                key: (
                    gettext("No source recorded")
                    if key is None
                    else OrderSourceType.DIRECT.label
                    if key == ""
                    else key
                )
                for key in keys
            }
        case GroupBy.VAT_RATE:
            return {key: f"{key.normalize():f}%" for key in keys}
        case GroupBy.PRODUCT | GroupBy.CATEGORY:
            model = Product if group_by == GroupBy.PRODUCT else ProductCategory
            names = {
                row.pk: row.safe_translation_getter("name", any_language=True)
                for row in model.objects.filter(pk__in=keys).prefetch_related(
                    "translations"
                )
            }
            missing = (
                gettext("Deleted product")
                if group_by == GroupBy.PRODUCT
                else gettext("Without a category")
            )
            return {
                key: missing if key is None else names.get(key) or f"#{key}"
                for key in keys
            }


# ── Columns: one definition for the page, the CSV and the XLSX ──────────

type Kind = str  # "money", "count" or "percent"
MONEY, COUNT, PERCENT = "money", "count", "percent"


@dataclass(frozen=True)
class Column:
    header: Any
    value: Callable[[Row], Decimal | int | float | None]
    kind: Kind


def display(kind: Kind, value: Decimal | float | None) -> str:
    if value is None:
        return "—"
    if kind == MONEY:
        return money(value)
    if kind == PERCENT:
        return percent_text(float(value), signed=True)
    return str(value)


def plain(kind: Kind, value: Decimal | float | None) -> str | int:
    """A cell of the CSV: a point, two decimals, nothing locale-bound."""
    if value is None:
        return ""
    if kind == MONEY:
        return f"{value:.2f}"
    if kind == PERCENT:
        return f"{value:.1f}"
    return int(value)


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


def _right(content: Any) -> dict[str, Any]:
    return {"content": content, "class": "text-right tabular-nums"}


def _table(report: Report) -> dict[str, Any]:
    columns = report.columns
    table_rows = [
        [report.period_name(row)]
        + [_right(display(c.kind, c.value(row))) for c in columns]
        for row in report.rows
    ]
    table_rows.append(
        [gettext("Total")]
        + [_right(display(c.kind, c.value(report.totals))) for c in columns]
    )
    return {
        "headers": [gettext("Period")]
        + [_right(column.header) for column in columns],
        "rows": table_rows,
    }


def _group_table(report: Report) -> dict[str, Any]:
    assert report.group_by is not None
    return {
        "headers": [GroupBy(report.group_by).label]
        + [_right(column.header) for column in _GROUP_COLUMNS],
        "rows": [
            [report.labels[row.group]]
            + [_right(display(c.kind, c.value(row))) for c in _GROUP_COLUMNS]
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


def _years() -> list[int]:
    """The years offered as shortcuts, newest first: from the first sale to
    this one."""
    current = timezone.localdate().year
    first = first_year() or current
    return list(range(current, first - 1, -1))


class TurnoverView(UnfoldSiteViewMixin, TemplateView):
    title = _("Turnover")
    permission_required = (PERMISSION,)
    template_name = "admin/turnover.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        form = _filter_form(self.request, max_rows=MAX_PAGE_ROWS)
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
                    for year in _years()
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
        form = _filter_form(request, max_rows=None)
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
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="{self.filename(report, "csv")}"'
        )
        response.write(BYTE_ORDER_MARK)
        group = report.group_by is not None
        sheet = _group_sheet(report) if group else _period_sheet(report)
        kinds = _kinds(report, group=group)
        writer = csv.writer(response)
        writer.writerow(sheet[0])
        for row in sheet[1:]:
            writer.writerow(
                [
                    plain(kind, cell) if kind != "text" else cell
                    for kind, cell in zip(kinds, row, strict=True)
                ]
            )
        return response


class TurnoverXlsxView(TurnoverExportView):
    """The periods, and the breakdown on a second sheet when asked for."""

    def render_report(self, report: Report) -> HttpResponse:
        workbook = Workbook(write_only=True)
        self._sheet(
            workbook,
            gettext("Turnover"),
            _period_sheet(report),
            report,
            group=False,
        )
        if report.group_by is not None:
            self._sheet(
                workbook,
                str(GroupBy(report.group_by).label),
                _group_sheet(report),
                report,
                group=True,
            )
        buffer = io.BytesIO()
        workbook.save(buffer)
        response = HttpResponse(
            buffer.getvalue(), content_type=XLSX_CONTENT_TYPE
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{self.filename(report, "xlsx")}"'
        )
        return response

    @staticmethod
    def _sheet(
        workbook: Workbook,
        title: str,
        rows: list[list[Any]],
        report: Report,
        *,
        group: bool,
    ) -> None:
        sheet = workbook.create_sheet(title=title[:EXCEL_TITLE_LENGTH])
        sheet.append(rows[0])
        kinds = _kinds(report, group=group)
        for row in rows[1:]:
            cells = []
            for kind, value in zip(kinds, row, strict=True):
                cell = WriteOnlyCell(sheet, value=value)
                if kind == MONEY:
                    cell.number_format = XLSX_MONEY_FORMAT
                cells.append(cell)
            sheet.append(cells)
