"""Reusable display helpers for ModelAdmin classes.

Centralises display logic that was duplicated across 15+ admin files —
status badges, money formatting, datetime formatting, two-line
"header" cells with avatar/initials. Always uses ``format_html`` so
every interpolated value is auto-escaped (no more
``mark_safe(conditional_escape(...))`` boilerplate that's easy to
forget on one field and ship an XSS hole).

Usage in a ModelAdmin:

    from admin.displays import status_badge, money

    @admin.display(description=_("Status"), ordering="status")
    def status_display(self, obj):
        return status_badge(obj.status, obj.get_status_display())
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _

# ── Status colour palette ─────────────────────────────────────────────
# Single source of truth for status → tone mapping. Tones map to
# Tailwind colour names (amber, sky, emerald, rose, violet, cyan).
# Keys are the lowercase status code; admin code passes the model
# enum value through ``str.lower()`` before lookup.

_STATUS_TONE: dict[str, str] = {
    # Order lifecycle
    "pending": "amber",
    "processing": "sky",
    "shipped": "cyan",
    "delivered": "emerald",
    "completed": "emerald",
    "canceled": "rose",
    "cancelled": "rose",
    "returned": "orange",
    "refunded": "violet",
    "partially_refunded": "violet",
    "failed": "rose",
    # Generic
    "active": "emerald",
    "inactive": "base",
    "draft": "base",
    "published": "emerald",
    "approved": "emerald",
    "rejected": "rose",
    "new": "sky",
    "ready": "emerald",
    "expired": "base",
}


def status_badge(code: str, label: str | None = None) -> SafeString:
    """Render a coloured pill for a status code.

    Args
    ----
    code
        The status enum value (e.g. ``"PENDING"`` or ``OrderStatus.PENDING``).
    label
        Optional override for the visible text. When ``None``, the
        code is title-cased (``"PENDING"`` → ``"Pending"``).

    Returns the safe HTML for a Tailwind pill that respects the global
    palette in ``_STATUS_TONE``.
    """

    code_norm = str(code).lower()
    tone = _STATUS_TONE.get(code_norm, "base")
    label = label or str(code).replace("_", " ").title()
    return format_html(
        '<span class="inline-flex items-center rounded-full px-2 py-0.5 '
        "text-xs font-medium bg-{tone}-100 text-{tone}-700 "
        'dark:bg-{tone}-900/40 dark:text-{tone}-300">{label}</span>',
        tone=tone,
        label=label,
    )


def boolean_badge(
    value: bool, label_true: str = "", label_false: str = ""
) -> SafeString:
    """Render a green ✓ / grey × pill for a boolean flag.

    Used for ``is_active``, ``approved``, ``featured``, etc. Without
    explicit labels, the pill shows just the icon.
    """

    if value:
        return format_html(
            '<span class="inline-flex items-center gap-1 rounded-full '
            "px-2 py-0.5 text-xs font-medium bg-emerald-100 "
            'text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">'
            '<span class="material-symbols-outlined text-sm!">check</span>'
            "{}</span>",
            label_true or "",
        )
    return format_html(
        '<span class="inline-flex items-center gap-1 rounded-full '
        "px-2 py-0.5 text-xs font-medium bg-base-100 text-base-600 "
        'dark:bg-base-800 dark:text-base-400">'
        '<span class="material-symbols-outlined text-sm!">close</span>'
        "{}</span>",
        label_false or "",
    )


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
