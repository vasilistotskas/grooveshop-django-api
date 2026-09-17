"""A store must not serve a locale its legal documents lack.

The legal routes render the tenant's own ContentPage for the ACTIVE
locale and throw a hard 404 on an empty body. Unlike parler on this
side, the storefront's ``extractTranslated`` does not fall back, so
enabling ``en`` on a store whose documents are Greek-only publishes a
storefront where the terms, privacy policy and cookie policy are
unreachable in English. delta-sigma is in exactly that state, and its
sitemap advertised three such URLs until they were gated out.

``missing_legal_translations`` is the rule, kept pure so it can be
tested without provisioning a schema; ``legal_translation_coverage``
reads it out of whichever schema the caller has switched to.
"""

from __future__ import annotations

import pytest

from page_config.defaults import legal_translation_coverage
from page_config.legal_documents import (
    LEGAL_DOCUMENT_SLUGS,
    missing_legal_translations,
)
from page_config.models import ContentPage, ContentPageTranslation

ALL_GREEK = {slug: {"el"} for slug in LEGAL_DOCUMENT_SLUGS}
ALL_BOTH = {slug: {"el", "en"} for slug in LEGAL_DOCUMENT_SLUGS}


class TestMissingLegalTranslations:
    def test_nothing_missing_when_every_document_has_the_locale(self) -> None:
        assert missing_legal_translations(ALL_BOTH, ["el", "en"]) == []

    def test_reports_every_document_for_an_untranslated_locale(self) -> None:
        missing = missing_legal_translations(ALL_GREEK, ["en"])

        assert missing == [(slug, "en") for slug in LEGAL_DOCUMENT_SLUGS]

    def test_reports_only_the_document_that_is_short(self) -> None:
        coverage = {**ALL_BOTH, "privacy": {"el"}}

        assert missing_legal_translations(coverage, ["en"]) == [
            ("privacy", "en")
        ]

    def test_a_slug_absent_from_coverage_counts_as_missing(self) -> None:
        # A store that never seeded the document at all, not merely one
        # that left a locale untranslated.
        assert missing_legal_translations({}, ["el"]) == [
            (slug, "el") for slug in LEGAL_DOCUMENT_SLUGS
        ]

    def test_checks_every_required_document(self) -> None:
        # The rule must not silently shrink if a slug is added to
        # LEGAL_DOCUMENT_SLUGS.
        reported = {
            slug for slug, _locale in missing_legal_translations({}, ["en"])
        }

        assert reported == set(LEGAL_DOCUMENT_SLUGS)

    def test_no_locales_asked_for_is_no_complaint(self) -> None:
        assert missing_legal_translations({}, []) == []


@pytest.mark.django_db
class TestLegalTranslationCoverage:
    def _page(self, slug: str, *, published: bool = True) -> ContentPage:
        return ContentPage.objects.create(slug=slug, is_published=published)

    def test_reports_the_locales_with_a_usable_body(self) -> None:
        page = self._page("terms")
        ContentPageTranslation.objects.create(
            master=page, language_code="el", title="Όροι", body="<p>κείμενο</p>"
        )
        ContentPageTranslation.objects.create(
            master=page, language_code="en", title="Terms", body="<p>text</p>"
        )

        assert legal_translation_coverage()["terms"] == {"el", "en"}

    def test_an_empty_body_does_not_count(self) -> None:
        # The route 404s on an empty body, so a blank row is exactly as
        # unreachable as no row — and parler will not fall back past a
        # row that exists, so nothing rescues it.
        page = self._page("privacy")
        ContentPageTranslation.objects.create(
            master=page, language_code="el", title="Απόρρητο", body="<p>κ</p>"
        )
        ContentPageTranslation.objects.create(
            master=page, language_code="en", title="Privacy", body="   "
        )

        assert legal_translation_coverage()["privacy"] == {"el"}

    def test_an_unpublished_document_does_not_count(self) -> None:
        page = self._page("cookies", published=False)
        ContentPageTranslation.objects.create(
            master=page, language_code="el", title="Cookies", body="<p>κ</p>"
        )

        assert "cookies" not in legal_translation_coverage()

    def test_ignores_pages_that_are_not_legal_documents(self) -> None:
        page = self._page("faq")
        ContentPageTranslation.objects.create(
            master=page, language_code="el", title="FAQ", body="<p>κ</p>"
        )

        assert "faq" not in legal_translation_coverage()
