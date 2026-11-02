from django.conf import settings
from django.test import TestCase

from page_config.content_pages import (
    DEFAULT_CONTENT_PAGES,
    seed_content_pages,
)
from page_config.legal_documents import (
    LEGAL_DOCUMENT_SLUGS,
    LEGAL_DOCUMENTS,
    render_legal_document,
)
from page_config.models import ContentPage


class TestSeedContentPages(TestCase):
    """Two kinds of seed, and the split is the point.

    The three legal documents ship with the platform's real text and
    PUBLISHED, because since 2026-09-17 the storefront renders these
    rows instead of markup compiled into it — an unpublished row is a
    store with no terms page, not a store with a blank one. Everything
    else ships as an unpublished prompt, because only the merchant can
    write it.
    """

    def test_creates_every_default_page(self):
        seed_content_pages()
        expected = set(DEFAULT_CONTENT_PAGES) | set(LEGAL_DOCUMENT_SLUGS)
        assert (
            set(ContentPage.objects.values_list("slug", flat=True)) == expected
        )

    def test_legal_documents_are_published_with_real_text(self):
        seed_content_pages()
        for slug in LEGAL_DOCUMENT_SLUGS:
            page = ContentPage.objects.get(slug=slug)
            assert page.is_published is True, slug
            body = page.translations.get(
                language_code=settings.PARLER_DEFAULT_LANGUAGE_CODE
            ).body
            # A real document, not a prompt: it carries the sectioned
            # structure the table of contents anchors to.
            assert "<section id=" in body, slug
            assert "Προσθέστε εδώ" not in body, slug

    def test_legal_documents_have_no_unsubstituted_tokens(self):
        seed_content_pages()
        for slug in LEGAL_DOCUMENT_SLUGS:
            body = (
                ContentPage.objects.get(slug=slug)
                .translations.get(
                    language_code=settings.PARLER_DEFAULT_LANGUAGE_CODE
                )
                .body
            )
            assert "{site_host}" not in body, slug
            assert "{store_name}" not in body, slug

    def test_placeholder_pages_stay_unpublished(self):
        seed_content_pages()
        for slug in DEFAULT_CONTENT_PAGES:
            page = ContentPage.objects.get(slug=slug)
            assert page.is_published is False, slug

    def test_pages_get_default_language_translation(self):
        seed_content_pages()
        page = ContentPage.objects.get(slug="terms")
        translation = page.translations.get(
            language_code=settings.PARLER_DEFAULT_LANGUAGE_CODE
        )
        assert translation.title == LEGAL_DOCUMENTS["terms"]["title"]

    def test_idempotent(self):
        seed_content_pages()
        first_count = ContentPage.objects.count()

        seed_content_pages()
        assert ContentPage.objects.count() == first_count

    def test_does_not_overwrite_merchant_edits(self):
        # A slug that already exists (merchant already customized it)
        # must be left alone — get_or_create only fills gaps.
        seed_content_pages()
        page = ContentPage.objects.get(slug="terms")
        translation = page.translations.get(
            language_code=settings.PARLER_DEFAULT_LANGUAGE_CODE
        )
        translation.body = "<p>Οι δικοί μας όροι.</p>"
        translation.save()

        seed_content_pages()
        translation.refresh_from_db()
        assert translation.body == "<p>Οι δικοί μας όροι.</p>"

    def test_returns_created_map(self):
        result = seed_content_pages()
        assert set(result) == set(DEFAULT_CONTENT_PAGES) | set(
            LEGAL_DOCUMENT_SLUGS
        )
        assert all(created is True for created in result.values())

        result_again = seed_content_pages()
        assert all(created is False for created in result_again.values())


class TestRenderLegalDocument(TestCase):
    def test_substitutes_both_tenant_values(self):
        body = render_legal_document(
            "terms", site_host="example.gr", store_name="Example"
        )
        assert "example.gr" in body
        assert "Example" in body
        assert "{site_host}" not in body
        assert "{store_name}" not in body

    def test_every_document_carries_sectioned_structure(self):
        # The table of contents is derived from these sections; a
        # document without them renders no jump list at all.
        for slug in LEGAL_DOCUMENT_SLUGS:
            body = render_legal_document(
                slug, site_host="example.gr", store_name="Example"
            )
            assert body.count("<section id=") >= 4, slug
            assert body.count("<h2>") == body.count("<section id="), slug
