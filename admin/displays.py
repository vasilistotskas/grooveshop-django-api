"""Reusable display helpers + the shared status-variant vocabulary.

Every status/state column in the admin renders through unfold's native
``@display(label=...)`` pills — no hand-rolled ``format_html`` badge
markup anywhere outside this module. Variant names are unfold's fixed
palette: ``success`` (green), ``info`` (blue), ``warning`` (orange),
``danger`` (red), ``primary`` (brand), ``default`` (neutral).

Usage in a ModelAdmin::

    from admin.displays import ORDER_STATUS_VARIANT, choice_label

    class OrderAdmin(BaseModelAdmin):
        list_display = ("status_display", ...)
        status_display = choice_label(
            "status",
            variants=ORDER_STATUS_VARIANT,
            description=_("Status"),
        )

The factory's display method returns ``(value, get_FOO_display())`` —
unfold's ``display_for_label`` unpacks the tuple, so the pill colour is
keyed off the stable enum value while the visible text stays
gettext-translated.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.contrib.admin import AdminSite
from django.db.models import Model
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.timesince import timesince
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext_lazy
from unfold.decorators import display

from order.enum.attribution import OrderSourceType
from order.enum.status import OrderStatus, PaymentStatus
from product.enum.review import ReviewStatus
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_boxnow.enum import BoxNowParcelState

# ── Shared variant maps (single source of truth) ──────────────────────
# Keyed by the TextChoices *values*; used by order/, shipping_*/ admins
# and the dashboard. Single-app enums (e.g. notification kinds) keep
# their maps local to that app's admin module.

ORDER_STATUS_VARIANT: dict[str, str] = {
    OrderStatus.PENDING: "warning",
    OrderStatus.PROCESSING: "info",
    OrderStatus.SHIPPED: "info",
    OrderStatus.DELIVERED: "success",
    OrderStatus.COMPLETED: "success",
    OrderStatus.CANCELED: "danger",
    OrderStatus.RETURNED: "warning",
    OrderStatus.REFUNDED: "primary",
}

PAYMENT_STATUS_VARIANT: dict[str, str] = {
    PaymentStatus.PENDING: "warning",
    PaymentStatus.PROCESSING: "info",
    PaymentStatus.COMPLETED: "success",
    PaymentStatus.FAILED: "danger",
    PaymentStatus.REFUNDED: "primary",
    PaymentStatus.PARTIALLY_REFUNDED: "primary",
    PaymentStatus.CANCELED: "danger",
}

ORDER_SOURCE_TYPE_VARIANT: dict[str, str] = {
    OrderSourceType.DIRECT: "default",
    OrderSourceType.CAMPAIGN: "primary",
    OrderSourceType.PAID: "warning",
    OrderSourceType.SOCIAL: "info",
    OrderSourceType.SEARCH: "success",
    OrderSourceType.REFERRAL: "default",
    OrderSourceType.AGENT: "primary",
}

REVIEW_STATUS_VARIANT: dict[str, str] = {
    ReviewStatus.NEW: "info",
    ReviewStatus.TRUE: "success",
    ReviewStatus.FALSE: "danger",
}

# One map covers both carriers: ACS and BoxNow state values are
# distinct strings, and the semantics align (terminal-good → success,
# terminal-bad → danger, in-flight → info, needs-attention → warning).
SHIPMENT_STATE_VARIANT: dict[str, str] = {
    # ACS
    AcsShipmentState.PENDING_CREATION: "default",
    AcsShipmentState.NEW: "info",
    AcsShipmentState.IN_TRANSIT: "info",
    AcsShipmentState.AT_DESTINATION: "info",
    AcsShipmentState.OUT_FOR_DELIVERY: "info",
    AcsShipmentState.DELIVERED: "success",
    AcsShipmentState.ATTEMPTED: "warning",
    AcsShipmentState.RETURNED: "warning",
    AcsShipmentState.CANCELED: "danger",
    AcsShipmentState.LOST: "danger",
    # BoxNow (values that differ from ACS)
    BoxNowParcelState.IN_DEPOT: "info",
    BoxNowParcelState.FINAL_DESTINATION: "info",
    BoxNowParcelState.EXPIRED: "warning",
    BoxNowParcelState.ACCEPTED_FOR_RETURN: "warning",
    BoxNowParcelState.ACCEPTED_TO_LOCKER: "info",
    BoxNowParcelState.MISSING: "danger",
}


def choice_label(
    field: str,
    *,
    variants: dict[str, str],
    description=None,
    ordering: str | None = None,
):
    """Build an unfold label column for a ``TextChoices`` model field.

    Returns a method suitable for direct assignment as a ModelAdmin
    class attribute and reference from ``list_display``. Sorting is
    preserved via ``ordering`` (defaults to the field itself).
    """

    @display(
        description=description or _(field.replace("_", " ").title()),
        ordering=ordering or field,
        label=variants,
    )
    def _col(self, obj):
        value = getattr(obj, field)
        if not value:
            return None
        return value, getattr(obj, f"get_{field}_display")()

    _col.__name__ = f"{field}_label"
    return _col


# ── Money + date formatting ───────────────────────────────────────────
# Everything follows the ACTIVE locale (Greek grouping "1.234,56" under
# el, "1,234.56" under en) and the current timezone - these used to be
# hard-coded to Greek separators and to strftime on UTC datetimes.


def money(amount: Decimal | float | None, currency: str = "€") -> str:
    """A money amount in the active locale; ``None`` reads as zero."""
    return currency + formats.number_format(
        amount or 0, decimal_pos=2, use_l10n=True, force_grouping=True
    )


def format_dt(
    dt: datetime | None,
    *,
    fmt: str = "SHORT_DATETIME_FORMAT",
    placeholder: str = "—",
) -> str:
    """A datetime in the current timezone and the active locale.

    ``fmt`` is a Django format name (``SHORT_DATE_FORMAT``) or a Django
    date-format string (``"d/m H:i"``), not an ``strftime`` pattern.
    """
    if dt is None:
        return placeholder
    return formats.date_format(timezone.localtime(dt), fmt)


# Django's own catalogue leaves these empty in Greek, so ``timesince``
# would answer in English under the Greek admin; ours are translated.
TIME_STRINGS = {
    "year": ngettext_lazy("%(num)d year", "%(num)d years", "num"),
    "month": ngettext_lazy("%(num)d month", "%(num)d months", "num"),
    "week": ngettext_lazy("%(num)d week", "%(num)d weeks", "num"),
    "day": ngettext_lazy("%(num)d day", "%(num)d days", "num"),
    "hour": ngettext_lazy("%(num)d hour", "%(num)d hours", "num"),
    "minute": ngettext_lazy("%(num)d minute", "%(num)d minutes", "num"),
}


def relative_time(dt: datetime | None, now: datetime | None = None) -> str:
    """How long ago, in the active language ("3 hours")."""
    if dt is None:
        return "—"
    return timesince(dt, now, time_strings=TIME_STRINGS, depth=1)


# ── Links ─────────────────────────────────────────────────────────────


def change_link(
    admin_site: AdminSite, model: type[Model], pk: Any, text: Any
) -> SafeString:
    """A link to a row's change page on the site serving this admin.

    Takes the model and primary key rather than the row, so a list
    column can link through a foreign key's id without loading the
    related row. Resolved on ``admin_site``'s own namespace: a
    hard-coded ``/admin/...`` path broke on the control plane and under
    a translated admin prefix.
    """
    opts = model._meta
    url = reverse(
        f"{admin_site.name}:{opts.app_label}_{opts.model_name}_change",
        args=[pk],
    )
    return format_html('<a href="{}">{}</a>', url, text)


# ── Table cells (Unfold's table.html escapes plain content) ─────────────


def link_cell(href: str, text: Any) -> SafeString:
    """A table cell holding Unfold's link component.

    Unfold's ``table.html`` escapes plain cell content, so markup must
    arrive already rendered (and ``text`` is escaped here).
    """
    return render_to_string(
        "unfold/components/link.html",
        {
            "href": href,
            "children": format_html("{}", text),
            "class": "font-medium text-primary-600 dark:text-primary-500",
        },
    )


def label_cell(text: Any, variant: str) -> SafeString:
    return render_to_string(
        "unfold/helpers/label.html",
        {"text": text, "variant": variant, "size": "md"},
    )


# ── Two-line "header" helpers (for @display(header=True)) ─────────────


def header_two_line(
    primary: Any,
    secondary: Any | None = None,
    initials: str | None = None,
    *,
    image_path: str | None = None,
    squared: bool = False,
    contained: bool = False,
) -> list[Any]:
    """Build the list that ``@display(header=True)`` expects.

    Unfold's ``display_header.html`` contract is positional:
    ``[title, subtitle, initials, image_dict]`` — the image dict MUST
    sit at index 3 (index 2 is the initials circle, rendered as raw
    text). Placing the dict at index 2 prints ``{'path': ...}`` inside
    the avatar circle — the "broken product images" bug on the prod
    changelist, 2026-07-12. Initials are always included as the
    template-level fallback when the image is absent.

    ``contained`` shows the whole image uncropped in a wider box
    (Unfold's ``as_background``): for logos, flags and icons, which the
    default round avatar would crop.
    """

    row = [primary, secondary or "", initials or _initials_from(primary)]
    if image_path:
        row.append(
            {"path": image_path, "squared": squared, "as_background": contained}
        )
    return row


def _initials_from(name: str | None) -> str:
    """Two-letter initials from a name's words ("Public (Platform)" → "PP")."""

    words = [
        word
        for word in (re.sub(r"\W", "", part) for part in (name or "").split())
        if word
    ]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[-1][0]).upper()
