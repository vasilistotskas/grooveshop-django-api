"""A blog category's icon: one of the icons the page builder offers.

The list is the storefront's, read from the store's own catalogue
(``builder.storefront``: the catalogue's ``icons``, each name with the
editor's label per language), never copied here, so a category can name
exactly the icons a builder section can, and the storefront can paint
every one of them.

:func:`check_icon` is the one rule both writers apply, the API's write
serializer and the admin form: nothing is checked on a guess, so when the
storefront does not answer a changed icon is refused (the rule of
``builder.storefront``), while an unchanged or cleared icon is never
checked, so an icon the builder stopped offering blocks no other edit of
its category.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from django.db import connection
from django.utils.translation import gettext_lazy as _

from builder import storefront
from core.storefront import StorefrontUnavailable
from core.utils.i18n import active_language_code


class IconCheck(StrEnum):
    """What :func:`check_icon` found; the two refusals are the codes the
    writers answer with."""

    VALID = "valid"
    UNKNOWN = "unknown_icon"
    UNAVAILABLE = "storefront_unavailable"


#: The refusal of an icon the store's catalogue does not offer.
UNKNOWN_ICON_MESSAGE = _("Pick one of the icons the store offers.")


def store_catalogue() -> storefront.Catalogue:
    """The current store's catalogue.

    Raises :class:`core.storefront.StorefrontUnavailable`.
    """
    return storefront.fetch_catalogue(connection.schema_name)


def check_icon(
    name: str,
    *,
    saved: str,
    catalogue: Callable[[], storefront.Catalogue] = store_catalogue,
) -> IconCheck:
    """Whether a category whose icon is *saved* may be written with
    *name*: an unchanged or cleared icon needs no catalogue, any other
    must be one the store's catalogue offers, and with no catalogue it
    cannot be checked. *catalogue* reads it, raising
    :class:`core.storefront.StorefrontUnavailable` when there is none: a
    caller that has already read it passes what it read."""
    if not name or name == saved:
        return IconCheck.VALID
    try:
        found = catalogue().icon(name)
    except StorefrontUnavailable:
        return IconCheck.UNAVAILABLE
    if found is None:
        return IconCheck.UNKNOWN
    return IconCheck.VALID


def icon_choices(catalogue: storefront.Catalogue) -> list[tuple[str, str]]:
    """Every icon *catalogue* offers as ``(name, label)``, labelled in the
    active language as ``settings.LANGUAGES`` names it
    (``active_language_code``: ``en-us`` reads ``en``, no active language
    reads the store's), the languages the catalogue labels every icon in
    (``builder.storefront._parse_icon``)."""
    language = active_language_code()
    return [(icon.name, icon.labels[language]) for icon in catalogue.icons]
