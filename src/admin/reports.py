"""What the store's report pages share: the filters, the labels, the columns
and the files.

The turnover and the profit pages take the same parameters (a range, a
period, a comparison with last year, a breakdown), name periods and groups
the same way, and export the same way, so that plumbing lives here and each
page keeps only its figures. A page, its CSV and its XLSX read one list of
``Column``s, so they cannot disagree about what a column is.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from django import forms
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from unfold.widgets import (
    INPUT_CLASSES,
    UnfoldAdminSelectWidget,
)

from admin.dashboard.base import percent_text
from admin.displays import money
from order.enum.attribution import OrderSourceType
from order.turnover import Bucket, GroupBy, GroupKey, buckets, first_year
from pay_way.enum.pay_way import PayWayEnum

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


def defaults() -> dict[str, str]:
    today = timezone.localdate()
    return {
        "start": date(today.year, 1, 1).isoformat(),
        "end": today.isoformat(),
        "bucket": Bucket.MONTH,
        "compare": COMPARE_YEAR,
        "group": "",
    }


def filter_form(request: HttpRequest, *, max_rows: int | None) -> FilterForm:
    """The request over the defaults: a parameter that is absent takes its
    default, one that is present (a ``compare`` left empty) is kept."""
    return FilterForm({**defaults(), **request.GET.dict()}, max_rows=max_rows)


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


type Kind = str  # "money", "count", "percent" or "share"


MONEY, COUNT, PERCENT, SHARE = "money", "count", "percent", "share"


@dataclass(frozen=True)
class Column:
    header: Any
    value: Callable[[Any], Decimal | int | float | None]
    kind: Kind


def display(kind: Kind, value: Decimal | float | None) -> str:
    if value is None:
        return "—"
    if kind == MONEY:
        return money(value)
    if kind == PERCENT:
        return percent_text(float(value), signed=True)
    if kind == SHARE:
        return percent_text(float(value))
    return str(value)


def plain(kind: Kind, value: Decimal | float | None) -> str | int:
    """A cell of the CSV: a point, two decimals, nothing locale-bound."""
    if value is None:
        return ""
    if kind == MONEY:
        return f"{value:.2f}"
    if kind in (PERCENT, SHARE):
        return f"{value:.1f}"
    return int(value)


def right(content: Any) -> dict[str, Any]:
    return {"content": content, "class": "text-right tabular-nums"}


def years() -> list[int]:
    """The years offered as shortcuts, newest first: from the first sale to
    this one."""
    current = timezone.localdate().year
    first = first_year() or current
    return list(range(current, first - 1, -1))


def csv_response(
    rows: Sequence[Sequence[Any]], kinds: Sequence[Kind], filename: str
) -> HttpResponse:
    """A sheet as a CSV: the headings, then the rows typed by ``kinds``.

    Amounts are plain numbers with a point, periods ISO (``2026-10``): a
    file for a spreadsheet or an accountant's import, not for reading, so
    nothing in it follows the viewer's locale but the headings.
    """
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write(BYTE_ORDER_MARK)
    writer = csv.writer(response)
    writer.writerow(rows[0])
    for row in rows[1:]:
        writer.writerow(
            [
                plain(kind, cell) if kind != "text" else cell
                for kind, cell in zip(kinds, row, strict=True)
            ]
        )
    return response


def xlsx_response(
    sheets: Sequence[tuple[str, Sequence[Sequence[Any]], Sequence[Kind]]],
    filename: str,
) -> HttpResponse:
    """Workbook of ``(title, rows, kinds)`` sheets, the first row of each
    its headings; money cells carry a number format."""
    workbook = Workbook(write_only=True)
    for title, rows, kinds in sheets:
        sheet = workbook.create_sheet(title=str(title)[:EXCEL_TITLE_LENGTH])
        sheet.append(list(rows[0]))
        for row in rows[1:]:
            cells = []
            for kind, value in zip(kinds, row, strict=True):
                cell = WriteOnlyCell(sheet, value=value)
                if kind == MONEY:
                    cell.number_format = XLSX_MONEY_FORMAT
                cells.append(cell)
            sheet.append(cells)
    buffer = io.BytesIO()
    workbook.save(buffer)
    response = HttpResponse(buffer.getvalue(), content_type=XLSX_CONTENT_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
