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

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from django.utils.translation import gettext_lazy as _
from unfold.decorators import display

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


def money(amount: Decimal | float | None, currency: str = "€") -> str:
    """Format a money amount with Greek thousands separator.

    Returns ``€0,00`` for None / zero so the cell isn't empty.
    """

    if amount is None:
        amount = 0
    val = float(amount)
    # Greek convention: dot for thousands, comma for decimals.
    formatted = (
        f"{val:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    )
    return f"{currency}{formatted}"


def format_dt(
    dt: datetime | None,
    *,
    fmt: str = "%d/%m/%Y %H:%M",
    placeholder: str = "—",
) -> str:
    """Format a datetime in 24-hour Greek-locale style by default.

    The default format `dd/mm/yyyy HH:MM` matches the Greek storefront
    convention. Pass ``fmt="%d/%m"`` for compact list cells.
    """

    if dt is None:
        return placeholder
    return dt.strftime(fmt)


def relative_time(dt: datetime | None, now: datetime | None = None) -> str:
    """Return a compact relative-time string (e.g. ``5λ``, ``3ω``, ``2η``).

    Uses Greek single-character suffixes for very short labels suited
    to dense list cells: λ=λεπτά, ω=ώρες, η=ημέρες.
    """

    if dt is None:
        return "—"
    if now is None:
        from django.utils import timezone  # noqa: PLC0415

        now = timezone.now()
    delta: timedelta = now - dt
    seconds = delta.total_seconds()
    if seconds < 60:
        return str(_("τώρα"))
    if seconds < 3600:
        return f"{int(seconds // 60)}λ"
    if seconds < 86400:
        return f"{int(seconds // 3600)}ω"
    return f"{int(seconds // 86400)}η"


# ── Two-line "header" helpers (for @display(header=True)) ─────────────


def header_two_line(
    primary: str,
    secondary: str | None = None,
    initials: str | None = None,
    *,
    image_path: str | None = None,
) -> list[Any]:
    """Build the 3-element list that ``@display(header=True)`` expects.

    See the unfold docs for `display(header=True)`. Returns
    ``[primary, secondary, initials | image_dict]``. Keeps the call
    sites symmetrical across User, Author, Order admins.
    """

    if image_path:
        return [
            primary,
            secondary or "",
            {"path": image_path, "squared": False},
        ]
    return [primary, secondary or "", initials or _initials_from(primary)]


def _initials_from(name: str | None) -> str:
    """Two-letter initials for a name string ("Vasileios T" → "VT")."""

    if not name:
        return "?"
    parts = [p for p in name.strip().split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()
