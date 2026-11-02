"""What the store's email page lists: every email type, grouped.

Pure data, no HTTP: ``emails.admin`` renders it. A row says whether the type
is on for the bound store, whether the store may switch it (a required type
never is), and which gate, if any, keeps it off whatever the switch says.
The switched-off set is read from the table, not from the pipeline's cache:
the page shows what was just written.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from django.utils.translation import gettext_lazy as _
from django_stubs_ext import StrOrPromise

from emails.figures import TypeFigures
from emails.models import EmailSwitch
from emails.registry import registry
from emails.types import Audience, EmailType, Owner

#: The page's two tabs: what customers receive, and what the store's team
#: receives. A type belongs to exactly one, by its audience.
CUSTOMERS = "customers"
TEAM = "team"
TAB_AUDIENCES = {
    CUSTOMERS: frozenset({Audience.CUSTOMER}),
    TEAM: frozenset({Audience.STORE_STAFF}),
}


class Condition(StrEnum):
    """The page's status filter."""

    ON = "on"
    OFF = "off"
    FAILING = "failing"


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
    figures: TypeFigures = field(default_factory=TypeFigures)

    @property
    def switchable(self) -> bool:
        return self.email_type.switchable

    @property
    def is_on(self) -> bool:
        """Sent today: not switched off and no gate closed."""
        return self.enabled and self.closed_gate is None


@dataclass(frozen=True, slots=True)
class SwitchGroup:
    area: str
    title: StrOrPromise
    rows: tuple[SwitchRow, ...]


def row_for(
    email_type: EmailType,
    *,
    enabled: bool,
    figures: TypeFigures | None = None,
) -> SwitchRow:
    closed = next((g for g in email_type.gates if not g.is_open()), None)
    return SwitchRow(
        email_type,
        enabled,
        closed.label if closed else None,
        figures or TypeFigures(),
    )


def _matches(row: SwitchRow, query: str, condition: str) -> bool:
    email_type = row.email_type
    if query and query not in (
        f"{email_type.key} {email_type.title} {email_type.description}".lower()
    ):
        return False
    match condition:
        case Condition.ON:
            return row.is_on
        case Condition.OFF:
            return not row.is_on
        case Condition.FAILING:
            return row.figures.failing > 0
    return True


def switch_groups(
    *,
    tab: str | None = None,
    query: str = "",
    condition: str = "",
    figures: dict[str, TypeFigures] | None = None,
) -> list[SwitchGroup]:
    """The store's email types by area, each with its state and figures.

    *tab* keeps one audience (``CUSTOMERS`` or ``TEAM``), *query* a text
    match on key, title and description, *condition* one :class:`Condition`;
    a group left with no row is dropped. Two queries at most, whatever the
    number of types: the switches and the (cached) figures.
    """
    off = set(EmailSwitch.objects.values_list("type_key", flat=True))
    known = figures or {}
    audiences = TAB_AUDIENCES.get(tab or "")
    needle = query.strip().lower()
    by_area: dict[str, list[SwitchRow]] = {area: [] for area in AREA_TITLES}
    for email_type in registry.types_for(Owner.STORE):
        if audiences is not None and email_type.audience not in audiences:
            continue
        row = row_for(
            email_type,
            enabled=email_type.key not in off,
            figures=known.get(email_type.key),
        )
        if _matches(row, needle, condition):
            by_area[email_type.area].append(row)
    return [
        SwitchGroup(area, AREA_TITLES[area], tuple(rows))
        for area, rows in by_area.items()
        if rows
    ]
