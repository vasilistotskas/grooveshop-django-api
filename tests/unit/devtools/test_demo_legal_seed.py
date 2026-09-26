"""The demo store's English legal documents are restored on every reset.

They used to be written once and then kept, so the demo's English
privacy policy never received the server-log section added after it was
first seeded, and the page stayed listed for review.
"""

from django.test import TestCase

from devtools.demo_legal import seed_english_legal_documents
from page_config.defaults import seed_content_pages
from page_config.legal_documents import LEGAL_TEXT_REVISION
from page_config.models import ContentPage, ContentPageTranslation


def _english(page: ContentPage) -> ContentPageTranslation:
    return page.translations.get(language_code="en")


class TestSeedEnglishLegalDocuments(TestCase):
    def setUp(self):
        seed_content_pages()
        # As the demo was in production: English from an earlier seed,
        # and the page never stamped because of it.
        self.page = ContentPage.objects.get(slug="privacy")
        ContentPageTranslation.objects.create(
            master=self.page,
            language_code="en",
            title="Old",
            body="<p>Old.</p>",
        )
        ContentPage.objects.filter(pk=self.page.pk).update(
            legal_text_revision=None
        )

    def test_restores_the_current_english_and_stamps_the_page(self):
        report = seed_english_legal_documents()

        self.page.refresh_from_db()
        assert 'id="server-logs"' in _english(self.page).body
        assert self.page.legal_text_revision == LEGAL_TEXT_REVISION
        assert report["stamped"] >= 1

    def test_a_rerun_with_current_text_changes_nothing(self):
        seed_english_legal_documents()
        report = seed_english_legal_documents()

        assert report.get("written", 0) == 0
        assert report.get("stamped", 0) == 0

    def test_a_greek_text_that_is_not_the_platforms_is_not_stamped(self):
        """The stamp says the whole page carries the update; an edited
        Greek body might not, so it stays listed for review."""
        greek = self.page.translations.get(language_code="el")
        greek.body = "<p>Κείμενο του καταστήματος.</p>"
        greek.save()

        seed_english_legal_documents()

        self.page.refresh_from_db()
        assert self.page.legal_text_revision is None
