"""A blog category's icon: one of the icons the page builder offers.

The list is the storefront's, read from the store's own catalogue
(``builder.storefront``: the catalogue's ``icons``, each name with the
editor's label per language), never copied here, so a category can name
exactly the icons a builder section can, and the storefront can paint
every one of them.

Nothing is checked on a guess: when the storefront does not answer,
:class:`core.storefront.StorefrontUnavailable` reaches the caller, which
refuses a changed icon (the API) or offers no choice (the admin), the rule
of ``builder.storefront``. An unchanged icon is never checked again, so an
icon the builder stopped offering does not block other edits of its
category.
"""

from __future__ import annotations

from django.db import connection
from django.utils.translation import get_language

from builder import storefront


def offered_icons() -> tuple[storefront.CatalogueIcon, ...]:
    """The icons the current store's catalogue offers.

    Raises :class:`core.storefront.StorefrontUnavailable`.
    """
    return storefront.fetch_catalogue(connection.schema_name).icons


def is_offered(name: str) -> bool:
    """Whether *name* is one of the icons the store offers.

    Raises :class:`core.storefront.StorefrontUnavailable`.
    """
    return any(icon.name == name for icon in offered_icons())


def icon_choices() -> list[tuple[str, str]]:
    """Every offered icon as ``(name, label)``, labelled in the active
    language (the catalogue labels every icon in every language,
    ``builder.storefront._parse_icon``).

    Raises :class:`core.storefront.StorefrontUnavailable`.
    """
    language = get_language()
    return [(icon.name, icon.labels[language]) for icon in offered_icons()]
