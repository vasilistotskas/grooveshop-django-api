"""What the store's email page lists: every email type, grouped.

Pure data, no HTTP: ``emails.admin`` renders it. A row says whether the type
is on for the bound store, whether the store may switch it (a required type
never is), and which gate, if any, keeps it off whatever the switch says.
The switched-off set is read from the table, not from the pipeline's cache:
the page shows what was just written.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _
from django_stubs_ext import StrOrPromise

from emails.models import EmailSwitch
from emails.registry import registry
from emails.types import EmailType, Owner

#: The groups of the page, in order. Every ``EmailType.area`` has a title
#: here (``tests/unit/emails/test_switches.py`` checks), so a type declared in
#: a new area fails CI rather than vanishing from the page.
AREA_TITLES: dict[str, StrOrPromise] = {
    "orders": _("Orders"),
    "shipping": _("Shipping"),
    "products": _("Products"),
    "gift_cards": _("Gift cards"),
    "wholesale": _("Wholesale"),
    "account": _("Customer accounts"),
    "team": _("To your team"),
    "ops": _("Alerts when something needs a person"),
}


@dataclass(frozen=True, slots=True)
class SwitchRow:
    email_type: EmailType
    #: Whether the store sends it (no switch row).
    enabled: bool
    #: The label of the first closed gate, shown as "Off: <label>".
    closed_gate: StrOrPromise | None

    @property
    def switchable(self) -> bool:
        return self.email_type.switchable


@dataclass(frozen=True, slots=True)
class SwitchGroup:
    area: str
    title: StrOrPromise
    rows: tuple[SwitchRow, ...]


def row_for(email_type: EmailType, *, enabled: bool) -> SwitchRow:
    closed = next((g for g in email_type.gates if not g.is_open()), None)
    return SwitchRow(email_type, enabled, closed.label if closed else None)


def switch_groups() -> list[SwitchGroup]:
    """The store's email types by area, each with its state."""
    off = set(EmailSwitch.objects.values_list("type_key", flat=True))
    by_area: dict[str, list[SwitchRow]] = {area: [] for area in AREA_TITLES}
    for email_type in registry.types_for(Owner.STORE):
        by_area[email_type.area].append(
            row_for(email_type, enabled=email_type.key not in off)
        )
    return [
        SwitchGroup(area, AREA_TITLES[area], tuple(rows))
        for area, rows in by_area.items()
        if rows
    ]
