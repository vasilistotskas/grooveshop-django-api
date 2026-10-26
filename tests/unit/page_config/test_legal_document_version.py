"""What an order accepted is the exact text of a legal page, kept apart.

``LegalDocumentVersion.objects.in_force`` snapshots the published title
and body of a legal page in one language, keyed by their hash: reused
while the text stays the same, a new version once the merchant edits it,
and never a fallback to another language, because the storefront's
legal routes render exactly the page's language and 404 an empty body.
"""

from __future__ import annotations

import hashlib

import pytest
from django.db.models import ProtectedError

from page_config.models import (
    ContentPage,
    ContentPageTranslation,
    LegalDocumentUnavailableError,
    LegalDocumentVersion,
)


def _page(slug: str = "terms", *, published: bool = True) -> ContentPage:
    return ContentPage.objects.create(slug=slug, is_published=published)


def _text(page: ContentPage, language: str, title: str, body: str):
    return ContentPageTranslation.objects.create(
        master=page, language_code=language, title=title, body=body
    )


@pytest.mark.django_db
class TestInForce:
    def test_snapshots_the_title_and_body_with_their_hash(self) -> None:
        page = _page()
        _text(page, "el", "Όροι χρήσης", "<p>Οι όροι.</p>")

        version = LegalDocumentVersion.objects.in_force("terms", "el")

        assert (version.page, version.language_code) == (page, "el")
        assert (version.title, version.body) == (
            "Όροι χρήσης",
            "<p>Οι όροι.</p>",
        )
        expected = hashlib.sha256(
            "Όροι χρήσης\n<p>Οι όροι.</p>".encode()
        ).hexdigest()
        assert version.content_hash == expected

    def test_the_same_text_is_one_version(self) -> None:
        _text(_page(), "el", "Όροι", "<p>Α</p>")

        first = LegalDocumentVersion.objects.in_force("terms", "el")
        second = LegalDocumentVersion.objects.in_force("terms", "el")

        assert first.pk == second.pk
        assert LegalDocumentVersion.objects.count() == 1

    def test_an_edit_is_a_new_version_and_the_old_one_is_kept(self) -> None:
        translation = _text(_page(), "el", "Όροι", "<p>Α</p>")
        before = LegalDocumentVersion.objects.in_force("terms", "el")

        translation.body = "<p>Β</p>"
        translation.save()
        after = LegalDocumentVersion.objects.in_force("terms", "el")

        assert after.pk != before.pk
        before.refresh_from_db()
        assert before.body == "<p>Α</p>"
        assert after.body == "<p>Β</p>"

    def test_each_language_is_its_own_version(self) -> None:
        page = _page()
        _text(page, "el", "Όροι", "<p>Α</p>")
        _text(page, "en", "Terms", "<p>A</p>")

        greek = LegalDocumentVersion.objects.in_force("terms", "el")
        english = LegalDocumentVersion.objects.in_force("terms", "en")

        assert (greek.language_code, english.language_code) == ("el", "en")
        assert english.body == "<p>A</p>"

    def test_no_fallback_to_another_language(self) -> None:
        # The storefront 404s the English page, so nothing was accepted
        # in English even though the Greek text exists.
        _text(_page(), "el", "Όροι", "<p>Α</p>")

        with pytest.raises(LegalDocumentUnavailableError) as raised:
            LegalDocumentVersion.objects.in_force("terms", "en")

        assert (raised.value.slug, raised.value.language_code) == (
            "terms",
            "en",
        )

    def test_an_empty_body_cannot_be_accepted(self) -> None:
        _text(_page(), "el", "Όροι", "   ")

        with pytest.raises(LegalDocumentUnavailableError):
            LegalDocumentVersion.objects.in_force("terms", "el")

    def test_an_unpublished_page_cannot_be_accepted(self) -> None:
        _text(_page(published=False), "el", "Όροι", "<p>Α</p>")

        with pytest.raises(LegalDocumentUnavailableError):
            LegalDocumentVersion.objects.in_force("terms", "el")

    def test_a_missing_page_cannot_be_accepted(self) -> None:
        with pytest.raises(LegalDocumentUnavailableError):
            LegalDocumentVersion.objects.in_force("privacy", "el")

    def test_a_page_with_accepted_versions_cannot_be_deleted(self) -> None:
        page = _page()
        _text(page, "el", "Όροι", "<p>Α</p>")
        LegalDocumentVersion.objects.in_force("terms", "el")

        with pytest.raises(ProtectedError):
            page.delete()
