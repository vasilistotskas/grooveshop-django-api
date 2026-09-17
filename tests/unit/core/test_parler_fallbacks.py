"""The parler fallback chain must actually reach a language.

``PARLER_LANGUAGES["default"]["fallbacks"]`` carried ``["en"]``, and for
an English reader that was a no-op: parler resolves a translated field
by walking ``[active_language] + fallbacks`` and **skipping the active
language** (``parler/models.py::_get_translated_model``), so with ``en``
active the only candidate was ``en`` itself. Nothing was tried, and
``TranslationDoesNotExist`` was raised.

That exception inherits ``AttributeError`` on purpose — "causes the
templates to handle the missing attributes silently", per its own
docstring — so Django templates rendered it as an empty string instead
of failing. ``{{ item.product.name }}`` in every order email is a live
parler field with no snapshot, so an order confirmation sent to an
English-speaking customer listed BLANK product names for every
Greek-only product. Confirmed against production data on 2026-09-17:
webside product #3 (``el`` only) rendered ``[]`` under ``en``.

These tests are about the CHAIN, not about one language pair, because
the same hole opens for any store whose authoring language is not the
one named in ``fallbacks``.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.template import engines
from django.utils import translation
from parler.utils import get_language_settings

from page_config.models import ContentPage, ContentPageTranslation

CONFIGURED_LANGUAGES = [
    entry["code"] for entry in settings.PARLER_LANGUAGES[None]
]


class TestFallbackChainShape:
    """Settings-level guarantees, independent of any stored row."""

    @pytest.mark.parametrize("language", CONFIGURED_LANGUAGES)
    def test_chain_offers_a_language_other_than_the_active_one(
        self, language: str
    ) -> None:
        # The exact defect: parler skips the active language, so a chain
        # consisting only of it degrades to "no fallback at all".
        fallbacks = get_language_settings(language)["fallbacks"]
        usable = [code for code in fallbacks if code != language]

        assert usable, (
            f"{language!r} has no usable fallback: parler skips the active "
            f"language, so {fallbacks!r} leaves nothing to try."
        )

    @pytest.mark.parametrize("language", CONFIGURED_LANGUAGES)
    def test_chain_reaches_the_authoring_language(self, language: str) -> None:
        # Content is authored in PARLER_DEFAULT_LANGUAGE_CODE, so every
        # other language must be able to reach it or it degrades to blank.
        fallbacks = get_language_settings(language)["fallbacks"]

        assert (
            language == settings.PARLER_DEFAULT_LANGUAGE_CODE
            or settings.PARLER_DEFAULT_LANGUAGE_CODE in fallbacks
        )

    def test_chain_is_derived_from_the_configured_languages(self) -> None:
        # A language added to _PARLER_LANG_TUPLE must not need a second,
        # manual edit to become reachable as a fallback.
        fallbacks = settings.PARLER_LANGUAGES["default"]["fallbacks"]

        assert set(fallbacks) == set(CONFIGURED_LANGUAGES)
        assert fallbacks[0] == settings.PARLER_DEFAULT_LANGUAGE_CODE

    def test_untranslated_rows_are_not_hidden(self) -> None:
        # Fallbacks only run when parler is allowed to serve a row it has
        # no translation of; hiding them would turn every gap into a
        # missing object instead.
        assert (
            settings.PARLER_LANGUAGES["default"]["hide_untranslated"] is False
        )


@pytest.mark.django_db
class TestFallbackBehaviour:
    """What a reader actually gets for a single-language document."""

    @pytest.fixture
    def greek_only_page(self) -> ContentPage:
        page = ContentPage.objects.create(
            slug="fallback-probe", is_published=True
        )
        ContentPageTranslation.objects.create(
            master=page,
            language_code=settings.PARLER_DEFAULT_LANGUAGE_CODE,
            title="Όροι Χρήσης",
            body="<p>κείμενο</p>",
        )
        return page

    @pytest.mark.parametrize("language", CONFIGURED_LANGUAGES)
    def test_falls_back_instead_of_raising(
        self, greek_only_page: ContentPage, language: str
    ) -> None:
        page = ContentPage.objects.get(pk=greek_only_page.pk)
        page.set_current_language(language)

        assert page.title == "Όροι Χρήσης"

    @pytest.mark.parametrize("language", CONFIGURED_LANGUAGES)
    def test_template_renders_a_name_rather_than_a_blank(
        self, greek_only_page: ContentPage, language: str
    ) -> None:
        # The order-email shape: `{{ obj.field }}` on a live parler field.
        # A blank here is the production bug, and it is silent because
        # TranslationDoesNotExist inherits AttributeError.
        template = engines["django"].from_string("[{{ page.title }}]")

        with translation.override(language):
            page = ContentPage.objects.get(pk=greek_only_page.pk)
            rendered = template.render({"page": page})

        assert rendered == "[Όροι Χρήσης]"

    def test_an_existing_but_empty_translation_is_not_overridden(
        self, greek_only_page: ContentPage
    ) -> None:
        # Fallbacks resolve a missing ROW, not an empty value — parler
        # stops as soon as it finds a translation. webside product #1
        # renders blank in English for exactly this reason, and no
        # fallback setting can fix it; it is a data defect.
        ContentPageTranslation.objects.create(
            master=greek_only_page, language_code="en", title="", body=""
        )

        page = ContentPage.objects.get(pk=greek_only_page.pk)
        page.set_current_language("en")

        assert page.title == ""
