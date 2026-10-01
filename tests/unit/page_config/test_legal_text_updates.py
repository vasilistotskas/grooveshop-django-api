"""The platform's legal-text revisions: the text, the seed, the flag.

``LEGAL_TEXT_UPDATES`` records each change to a seeded legal document.
The data migration applies one to pages still on platform text; these
tests cover everything around it: that the text says what the platform
actually does, that a new tenant is seeded as current, and that a page
the rollout could not touch is flagged to its merchant until they mark
it reviewed.
"""

import pytest
from django.conf import settings
from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.management import call_command
from django.test import RequestFactory, TestCase

from admin.dashboard.store.widgets import StoreAlerts
from core.utils.sanitize import sanitize_html
from page_config.admin import ContentPageAdmin
from page_config.defaults import pending_legal_reviews, seed_content_pages
from page_config.legal_documents import (
    LEGAL_DOCUMENT_SLUGS,
    LEGAL_DOCUMENTS,
    LEGAL_TEXT_REVISION,
    LEGAL_TEXT_UPDATES,
    PRIVACY_SERVER_LOGS_SECTION,
    pending_legal_updates,
    render_legal_document,
)
from page_config.models import ContentPage, ContentPageTranslation

User = get_user_model()
LANGUAGE = settings.PARLER_DEFAULT_LANGUAGE_CODE


class TestServerLogsSection:
    @pytest.mark.parametrize("language", ["el", "en"])
    def test_is_one_anchored_section(self, language):
        section = PRIVACY_SERVER_LOGS_SECTION[language]
        assert section.count('<section id="server-logs">') == 1
        assert section.count("<h2>") == 1
        assert section.rstrip().endswith("</section>")

    @pytest.mark.parametrize("language", ["el", "en"])
    def test_states_the_retention_the_platform_runs(self, language):
        """14 days is VictoriaLogs' retentionPeriod. If the infra value
        moves, this text is wrong until a new revision ships."""
        assert "14" in PRIVACY_SERVER_LOGS_SECTION[language]

    @pytest.mark.parametrize("language", ["el", "en"])
    def test_names_the_processors_and_the_authority(self, language):
        section = PRIVACY_SERVER_LOGS_SECTION[language]
        for name in ("Cloudflare", "Hetzner", "www.dpa.gr", "Data Privacy"):
            assert name in section, name

    @pytest.mark.parametrize("language", ["el", "en"])
    def test_states_the_right_to_object_separately(self, language):
        """Art. 21(4): presented clearly and separately. Its own
        paragraph, opening with its own label."""
        label = {"el": "Δικαίωμα εναντίωσης.", "en": "Right to object."}
        assert (
            f"<p><strong>{label[language]}</strong>"
            in (PRIVACY_SERVER_LOGS_SECTION[language])
        )

    @pytest.mark.parametrize("language", ["el", "en"])
    def test_names_the_authority_by_website_only(self, language):
        """The Hellenic DPA asks controllers to cite its website and
        not its e-mail address or telephone number."""
        section = PRIVACY_SERVER_LOGS_SECTION[language]
        assert "@dpa.gr" not in section
        assert "210" not in section

    @pytest.mark.parametrize("language", ["el", "en"])
    def test_survives_sanitization_byte_for_byte(self, language):
        """The rollout recognises platform text by exact comparison, and
        the live model sanitises on save. A sanitiser that rewrote any of
        this would make seeded rows unrecognisable to the next rollout."""
        rendered = PRIVACY_SERVER_LOGS_SECTION[language].replace(
            "{site_host}", "example.gr"
        )
        rendered = rendered.replace("{store_name}", "Example")
        assert sanitize_html(rendered) == rendered

    def test_the_privacy_document_ends_with_it(self):
        assert LEGAL_DOCUMENTS["privacy"]["body"].endswith(
            PRIVACY_SERVER_LOGS_SECTION["el"]
        )

    def test_the_whole_privacy_document_survives_sanitization(self):
        body = render_legal_document(
            "privacy", site_host="example.gr", store_name="Example"
        )
        assert sanitize_html(body) == body

    def test_both_languages_carry_the_same_structure(self):
        el, en = (PRIVACY_SERVER_LOGS_SECTION[lang] for lang in ("el", "en"))
        for tag in ("<p>", "<li>", "<strong>"):
            assert el.count(tag) == en.count(tag), tag


class TestPendingLegalUpdates:
    def test_current_revision_is_the_newest_update(self):
        assert LEGAL_TEXT_REVISION == max(
            u.revision for u in LEGAL_TEXT_UPDATES
        )

    def test_an_unstamped_page_has_every_update_to_its_slug(self):
        assert [u.revision for u in pending_legal_updates("privacy", None)] == [
            u.revision for u in LEGAL_TEXT_UPDATES if u.slug == "privacy"
        ]

    def test_a_current_page_has_none(self):
        assert pending_legal_updates("privacy", LEGAL_TEXT_REVISION) == []

    def test_a_slug_nobody_updated_has_none(self):
        assert pending_legal_updates("terms", None) == []

    def test_updates_name_only_legal_documents(self):
        for update in LEGAL_TEXT_UPDATES:
            assert update.slug in LEGAL_DOCUMENT_SLUGS


def _privacy(body: str, *, revision: int | None = None) -> ContentPage:
    page = ContentPage.objects.create(
        slug="privacy", is_published=True, legal_text_revision=revision
    )
    ContentPageTranslation.objects.create(
        master=page, language_code=LANGUAGE, title="T", body=body
    )
    return page


def _request(user):
    request = RequestFactory().get("/admin/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


class TestSeedAndReview(TestCase):
    def test_seed_stamps_legal_documents_as_current(self):
        seed_content_pages()

        for slug in LEGAL_DOCUMENT_SLUGS:
            page = ContentPage.objects.get(slug=slug)
            assert page.legal_text_revision == LEGAL_TEXT_REVISION, slug
        assert pending_legal_reviews() == []

    def test_seed_leaves_other_pages_unstamped(self):
        seed_content_pages()

        assert ContentPage.objects.get(slug="faq").legal_text_revision is None

    def test_seeded_privacy_policy_carries_the_section(self):
        seed_content_pages()

        body = (
            ContentPage.objects.get(slug="privacy")
            .translations.get(language_code=LANGUAGE)
            .body
        )
        assert 'id="server-logs"' in body

    def test_an_unstamped_privacy_page_is_pending(self):
        page = _privacy("<p>Δική μας.</p>")

        pending = pending_legal_reviews()

        assert [(p.pk, u.revision) for p, u in pending] == [
            (page.pk, LEGAL_TEXT_REVISION)
        ]

    def test_admin_shows_the_text_to_add_on_a_pending_page(self):
        page = _privacy("<p>Δική μας.</p>")
        model_admin = ContentPageAdmin(ContentPage, django_admin.site)
        user = User.objects.create_superuser(
            email="owner@example.com", password="x"
        )

        fieldsets = model_admin.get_fieldsets(_request(user), page)
        html = str(model_admin.legal_update_text(page))

        assert fieldsets[0][1]["fields"] == ("legal_update_text",)
        assert 'id="server-logs"' in html
        # Both languages, so a bilingual store has its English text too.
        assert "Αρχεία καταγραφής διακομιστή" in html
        assert "Server logs" in html
        assert "{store_name}" not in html

    def test_admin_escapes_the_store_name_it_substitutes(self):
        page = _privacy("<p>Δική μας.</p>")
        model_admin = ContentPageAdmin(ContentPage, django_admin.site)
        from page_config import admin as page_admin

        original = page_admin.tenant_document_context
        page_admin.tenant_document_context = lambda: (
            "example.gr",
            "<script>x</script>",
        )
        try:
            html = str(model_admin.legal_update_text(page))
        finally:
            page_admin.tenant_document_context = original

        assert "<script>" not in html

    def test_admin_adds_nothing_to_a_current_page(self):
        page = _privacy("<p>x</p>", revision=LEGAL_TEXT_REVISION)
        model_admin = ContentPageAdmin(ContentPage, django_admin.site)
        user = User.objects.create_superuser(
            email="owner@example.com", password="x"
        )

        fieldsets = model_admin.get_fieldsets(_request(user), page)

        assert all(
            "legal_update_text" not in options["fields"]
            for _name, options in fieldsets
        )

    def test_marking_reviewed_clears_the_flag(self):
        _privacy("<p>Δική μας.</p>")
        faq = ContentPage.objects.create(slug="faq")
        model_admin = ContentPageAdmin(ContentPage, django_admin.site)
        user = User.objects.create_superuser(
            email="owner@example.com", password="x"
        )

        model_admin.mark_legal_update_reviewed(
            _request(user), ContentPage.objects.all()
        )

        assert pending_legal_reviews() == []
        faq.refresh_from_db()
        # Not a legal document: nothing to review, nothing stamped.
        assert faq.legal_text_revision is None


def _legal_alerts(user) -> list:
    """The dashboard's legal-update alerts, as ``user`` would see them."""
    request = RequestFactory().get("/admin/")
    request.user = user
    return StoreAlerts(request=request)._legal_updates()


class TestDashboardBanner(TestCase):
    def test_lists_pending_pages_for_someone_who_edits_them(self):
        page = _privacy("<p>Δική μας.</p>")
        user = User.objects.create_superuser(
            email="owner@example.com", password="x"
        )

        alerts = _legal_alerts(user)

        assert len(alerts) == 1
        assert (
            f"/page_config/contentpage/{page.pk}/change/"
            in alerts[0]["items"][0]
        )

    def test_hidden_from_someone_who_cannot_edit_them(self):
        _privacy("<p>Δική μας.</p>")
        user = User.objects.create_user(
            email="viewer@example.com", password="x"
        )

        assert _legal_alerts(user) == []

    def test_empty_when_nothing_is_pending(self):
        _privacy("<p>x</p>", revision=LEGAL_TEXT_REVISION)
        user = User.objects.create_superuser(
            email="owner@example.com", password="x"
        )

        assert _legal_alerts(user) == []


class TestLegalTextStatusCommand(TestCase):
    def test_reports_success_with_no_tenants_behind(self):
        from io import StringIO

        out = StringIO()
        call_command("legal_text_status", stdout=out)

        assert "current" in out.getvalue()
