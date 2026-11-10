"""A document's root settings as the store reads them in a language."""

from __future__ import annotations

from builder.published import Source, resolve_document

EMPTY = {"sections": {}, "order": []}


def test_a_translated_root_setting_stands_without_a_default_one():
    # The editor drops an empty ``settings``: the English title may be the
    # only one the page has.
    source = Source(EMPTY, {"en": {"@document": {"/seoTitle": "Home"}}})

    assert resolve_document("template:home", source, "en")["settings"] == {
        "seoTitle": "Home"
    }


def test_the_default_language_reads_no_translation():
    source = Source(EMPTY, {"en": {"@document": {"/seoTitle": "Home"}}})

    assert "settings" not in resolve_document("template:home", source, "el")


def test_a_translation_is_laid_over_the_default_settings():
    source = Source(
        {**EMPTY, "settings": {"seoTitle": "Αρχική", "heading": "Γεια"}},
        {"en": {"@document": {"/seoTitle": "Home"}}},
    )

    assert resolve_document("template:home", source, "en")["settings"] == {
        "seoTitle": "Home",
        "heading": "Γεια",
    }
