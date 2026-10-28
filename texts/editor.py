"""What the text editor shows: the catalogue merged with a store's
overrides, searched, filtered and paged.

Pure data, no HTTP: ``texts.admin`` renders it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from tenant.staff_actors import resolve_staff
from texts.areas import Area, area_by_code, area_of
from texts.models import TextOverride, TextSurface
from texts.storefront import Catalogue, CatalogueEntry, MessageShape

PER_PAGE = 20

TRUTHY = frozenset({"1", "true", "on", "yes"})


def language_name(code: str) -> str:
    return dict(settings.LANGUAGES).get(code, code)


def row_id(namespace: str, key: str) -> str:
    """A stable DOM id for one text."""
    digest = hashlib.sha256(f"{namespace}\0{key}".encode()).hexdigest()
    return f"text-{digest[:12]}"


@dataclass(frozen=True)
class Chip:
    """A placeholder the editor can insert: the token and its name."""

    token: str
    label: str


def chips_for(shape: MessageShape | None) -> tuple[Chip, ...]:
    """The placeholders of a default, as the text that inserts them.

    ``params`` are ``named:<name>``, ``list:<index>``, ``linked:<key>``
    and ``linked:<key>|<modifier>`` (the storefront's ``MessageShape``).
    """
    if shape is None:
        return ()
    chips = []
    for param in shape.params:
        kind, _sep, rest = param.partition(":")
        if kind in {"named", "list"}:
            token = "{" + rest + "}"
        elif kind == "linked":
            name, _bar, modifier = rest.partition("|")
            token = f"@.{modifier}:{name}" if modifier else f"@:{name}"
        else:
            continue
        chips.append(Chip(token=token, label=rest))
    return tuple(chips)


@dataclass(frozen=True)
class Cell:
    language: str
    default: str | None
    value: str
    overridden: bool
    chips: tuple[Chip, ...]
    plural_cases: int
    #: The rendered textarea, set by the admin (it knows the URLs).
    control: str = ""
    #: Why the last save refused this language, if it did.
    error: ValidationError | None = None

    @property
    def language_name(self) -> str:
        return language_name(self.language)

    @property
    def missing(self) -> bool:
        """No text at all in this language: no default, no override."""
        return self.default is None and not self.overridden


@dataclass(frozen=True)
class Row:
    entry: CatalogueEntry
    area: Area
    dom_id: str
    cells: tuple[Cell, ...]
    preview_url: str | None = None
    #: htmx attributes of the Reset and History buttons (the admin builds
    #: them: it knows the URLs).
    reset_attrs: str = ""
    history_attrs: str = ""

    @property
    def vals(self) -> str:
        """``hx-vals`` naming this text, for requests outside the form."""
        return json.dumps(
            {"namespace": self.entry.namespace, "key": self.entry.key}
        )

    @property
    def changed(self) -> bool:
        return any(cell.overridden for cell in self.cells)

    @property
    def locked(self) -> bool:
        return self.entry.locked


Overrides = Mapping[tuple[str, str], Mapping[str, TextOverride]]


def load_overrides() -> dict[tuple[str, str], dict[str, TextOverride]]:
    grouped: dict[tuple[str, str], dict[str, TextOverride]] = {}
    for row in TextOverride.objects.filter(surface=TextSurface.STOREFRONT):
        grouped.setdefault((row.namespace, row.key), {})[row.language] = row
    return grouped


def build_row(
    entry: CatalogueEntry,
    locales: Iterable[str],
    overrides: Mapping[str, TextOverride],
) -> Row:
    cells = []
    for language in locales:
        default = entry.defaults.get(language)
        override = overrides.get(language)
        shape = entry.placeholders.get(language)
        cells.append(
            Cell(
                language=language,
                default=default,
                value=override.value if override else (default or ""),
                overridden=override is not None,
                chips=chips_for(shape),
                plural_cases=shape.plural_cases if shape else 1,
            )
        )
    return Row(
        entry=entry,
        area=area_of(entry.namespace, entry.key),
        dom_id=row_id(entry.namespace, entry.key),
        cells=tuple(cells),
    )


@dataclass(frozen=True)
class Filters:
    q: str = ""
    area: str = ""
    changed: bool = False
    missing: bool = False

    @classmethod
    def from_query(cls, query: Mapping[str, str]) -> Filters:
        area = query.get("area", "")
        return cls(
            q=query.get("q", "").strip(),
            area=area if area_by_code(area) else "",
            changed=query.get("changed", "").lower() in TRUTHY,
            missing=query.get("missing", "").lower() in TRUTHY,
        )

    def as_query(self) -> dict[str, str]:
        params = {
            "q": self.q,
            "area": self.area,
            "changed": "1" if self.changed else "",
            "missing": "1" if self.missing else "",
        }
        return {name: value for name, value in params.items() if value}


def _haystack(
    entry: CatalogueEntry, overrides: Mapping[str, TextOverride]
) -> str:
    parts = [
        entry.namespace,
        entry.key,
        *entry.defaults.values(),
        *(row.value for row in overrides.values()),
    ]
    return "\n".join(parts).casefold()


def _is_missing(
    entry: CatalogueEntry,
    locales: Iterable[str],
    overrides: Mapping[str, TextOverride],
) -> bool:
    return any(
        language not in entry.defaults and language not in overrides
        for language in locales
    )


def select(
    catalogue: Catalogue, overrides: Overrides, filters: Filters
) -> list[CatalogueEntry]:
    """The entries matching *filters*, in catalogue order."""
    words = filters.q.casefold().split()
    matched = []
    for entry in catalogue.entries:
        own = overrides.get((entry.namespace, entry.key), {})
        if filters.area and (
            area_of(entry.namespace, entry.key).code != filters.area
        ):
            continue
        if filters.changed and not own:
            continue
        if filters.missing and not _is_missing(entry, catalogue.locales, own):
            continue
        if words:
            haystack = _haystack(entry, own)
            if not all(word in haystack for word in words):
                continue
        matched.append(entry)
    return matched


def area_counts(
    catalogue: Catalogue, overrides: Overrides
) -> dict[str, tuple[int, int]]:
    """``area code -> (texts, changed texts)`` for the area filter."""
    counts: dict[str, list[int]] = {}
    for entry in catalogue.entries:
        code = area_of(entry.namespace, entry.key).code
        total = counts.setdefault(code, [0, 0])
        total[0] += 1
        if (entry.namespace, entry.key) in overrides:
            total[1] += 1
    return {code: (a, b) for code, (a, b) in counts.items()}


@dataclass(frozen=True)
class HistoryItem:
    history_id: int
    language: str
    when: datetime
    who: str
    kind: str
    reason: str
    before: str
    after: str
    restorable: bool

    @property
    def language_name(self) -> str:
        return language_name(self.language)


def history_for(entry: CatalogueEntry) -> list[HistoryItem]:
    """Every change of one text, newest first, with before and after.

    "Before" and "after" are the text a shopper saw: a removed override
    reads as the platform's default. The newest entry of a language is
    the current state, so it has nothing to restore.
    """
    records = list(
        TextOverride.history.filter(
            surface=TextSurface.STOREFRONT,
            namespace=entry.namespace,
            key=entry.key,
        ).order_by("-history_date", "-history_id")
    )
    staff = resolve_staff(r.history_user_id for r in records)
    by_language: dict[str, list] = {}
    for record in records:
        by_language.setdefault(record.language, []).append(record)

    kinds = {"+": _("Created"), "~": _("Edited"), "-": _("Reset")}
    items = []
    for language, own in by_language.items():
        default = entry.defaults.get(language, "")
        for index, record in enumerate(own):
            older = own[index + 1] if index + 1 < len(own) else None
            after = default if record.history_type == "-" else record.value
            before = (
                default
                if older is None or older.history_type == "-"
                else older.value
            )
            actor = staff.get(record.history_user_id)
            items.append(
                HistoryItem(
                    history_id=record.history_id,
                    language=language,
                    when=record.history_date,
                    who=(
                        (actor.get_full_name() or actor.get_username())
                        if actor
                        else ""
                    ),
                    kind=str(kinds.get(record.history_type, "")),
                    reason=record.history_change_reason or "",
                    before=before,
                    after=after,
                    restorable=index > 0,
                )
            )
    items.sort(key=lambda item: item.when, reverse=True)
    return items


def preview_language(locales: Iterable[str]) -> str:
    """The language the storefront preview opens in: the admin's own when
    the storefront has it, else the default."""
    current = (get_language() or settings.LANGUAGE_CODE).split("-")[0]
    return current if current in tuple(locales) else settings.LANGUAGE_CODE
