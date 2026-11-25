"""The catalogue's ``icons``: every icon the builder offers, each with the
editor's label per language, for a field that holds one icon outside a
block (a blog category's)."""

from __future__ import annotations

import pytest

from builder.storefront import CatalogueIcon, parse_catalogue
from core.storefront import StorefrontUnavailable
from tests.utils.builder import HASH, default_icons


def payload(**extra):
    return {"hash": HASH, "blocks": [], "icons": default_icons(), **extra}


def test_reads_each_icon_with_its_label_in_every_language():
    catalogue = parse_catalogue(payload())

    assert catalogue.icon("i-fa6-solid-robot") == CatalogueIcon(
        name="i-fa6-solid-robot", labels={"el": "Ρομπότ", "en": "Robot"}
    )
    assert catalogue.icon("i-mdi-gamepad") is None


def test_a_catalogue_without_icons_is_refused():
    body = payload()
    del body["icons"]

    with pytest.raises(StorefrontUnavailable):
        parse_catalogue(body)


@pytest.mark.parametrize(
    "icon",
    [
        {"name": "", "labels": {"el": "Α", "en": "A"}},
        {"name": "i-lucide-zap", "labels": {"el": "Κεραυνός"}},
        {"name": "i-lucide-zap", "labels": {"el": "", "en": "Lightning"}},
        {"name": "i-lucide-zap", "labels": "Lightning"},
    ],
)
def test_an_icon_without_a_name_or_a_label_per_language_is_refused(icon):
    with pytest.raises(StorefrontUnavailable):
        parse_catalogue(payload(icons=[icon]))
