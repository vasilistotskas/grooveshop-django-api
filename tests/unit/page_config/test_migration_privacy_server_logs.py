"""Exercises page_config/migrations/0032_privacy_server_logs_section.

The rollout of revision 1 of the platform's legal text. Two properties
matter, and each has a test that fails when it breaks:

- a privacy policy still carrying the platform's text gets the new
  section and is stamped as current;
- a merchant's text is never rewritten and never stamped, so it stays
  flagged in their admin until they act.

Rows are written through the LIVE model, whose ``save()`` sanitises the
body, because that is how ``seed_content_pages`` stored every tenant
provisioned after 0021. A sanitiser that altered the platform text would
make the migration miss those rows, and these tests would say so.
"""

import importlib
from datetime import timedelta

from django.apps import apps as live_apps
from django.conf import settings
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from django_tenants.utils import get_public_schema_name

from page_config.legal_documents import (
    LEGAL_DOCUMENTS,
    PRIVACY_SERVER_LOGS_SECTION,
)
from page_config.models import ContentPage, ContentPageTranslation

migration_module = importlib.import_module(
    "page_config.migrations.0032_privacy_server_logs_section"
)

LANGUAGE = settings.PARLER_DEFAULT_LANGUAGE_CODE
SCHEMA = "a-real-tenant"
HOST = "a-real-tenant.example.gr"
STORE = "A Real Tenant"


class _FakeConnection:
    def __init__(self, schema_name: str):
        self.schema_name = schema_name
        self.alias = connection.alias

    def cursor(self):
        return connection.cursor()


class _FakeSchemaEditor:
    def __init__(self, schema_name: str):
        self.connection = _FakeConnection(schema_name)


def _make_tenant() -> None:
    from tenant.models import Tenant, TenantDomain

    tenant = Tenant(schema_name=SCHEMA, name=STORE, store_name=STORE)
    tenant.auto_create_schema = False
    tenant.save()
    TenantDomain.objects.create(tenant=tenant, domain=HOST, is_primary=True)


def _run(schema_name: str = SCHEMA) -> None:
    migration_module.add_server_logs_section(
        live_apps, _FakeSchemaEditor(schema_name)
    )


def _previous(host: str = HOST, store: str = STORE) -> str:
    return migration_module._render(migration_module.PREVIOUS_BODY, host, store)


def _current(host: str = HOST, store: str = STORE) -> str:
    return migration_module._render(migration_module.CURRENT_BODY, host, store)


def _seed(body: str, *, language: str = LANGUAGE) -> ContentPage:
    page = ContentPage.objects.filter(slug="privacy").first()
    if page is None:
        page = ContentPage.objects.create(slug="privacy", is_published=True)
    ContentPageTranslation.objects.create(
        master=page, language_code=language, title="T", body=body
    )
    return page


def _page() -> ContentPage:
    return ContentPage.objects.get(slug="privacy")


def _body(language: str = LANGUAGE) -> str:
    return _page().translations.get(language_code=language).body


class TestPrivacyServerLogsSection(TestCase):
    def setUp(self):
        _make_tenant()

    def test_adds_the_section_to_untouched_platform_text(self):
        _seed(_previous())

        _run()

        assert _body() == _current()
        assert 'id="server-logs"' in _body()
        assert HOST in _body()
        assert _page().legal_text_revision == 1

    def test_moves_the_last_updated_date(self):
        page = _seed(_previous())
        ContentPage.objects.filter(pk=page.pk).update(
            updated_at=timezone.now() - timedelta(days=30)
        )
        before = _page().updated_at

        _run()

        assert _page().updated_at > before

    def test_recognises_the_fallback_identity_0021_seeded(self):
        host = getattr(settings, "APP_MAIN_HOST_NAME", "") or ""
        store = getattr(settings, "SITE_NAME", "") or ""
        _seed(_previous(host, store))

        _run()

        # Rewritten with the tenant's own identity, as 0022 would have.
        assert _body() == _current()
        assert _page().legal_text_revision == 1

    def test_recognises_a_store_seeded_before_its_domain_existed(self):
        """A schema is migrated when the Tenant row is saved, before its
        TenantDomain rows: 0021/0022 render its own name with the
        fallback host. Found by the multi-tenant lane, whose tenant is
        provisioned exactly that way."""
        host = getattr(settings, "APP_MAIN_HOST_NAME", "") or ""
        _seed(_previous(host, STORE))

        _run()

        assert _body() == _current()
        assert _page().legal_text_revision == 1

    def test_leaves_a_merchant_policy_alone_and_unstamped(self):
        """The property this migration must not violate."""
        own = "<p>Η δική μας πολιτική απορρήτου.</p>"
        _seed(own)

        _run()

        assert _body() == own
        assert _page().legal_text_revision is None

    def test_an_edited_platform_text_counts_as_the_merchants(self):
        edited = _previous().replace("newsletters", "ενημερωτικά δελτία")
        _seed(edited)

        _run()

        assert _body() == edited
        assert _page().legal_text_revision is None

    def test_another_language_keeps_the_page_flagged(self):
        """The platform writes no English document, so an English
        translation is the merchant's: the Greek text is updated, but the
        page is not stamped, and the admin keeps asking for the English
        section."""
        english = "<p>Our own privacy policy.</p>"
        _seed(_previous())
        _seed(english, language="en")

        _run()

        assert _body() == _current()
        assert _body("en") == english
        assert _page().legal_text_revision is None

    def test_an_empty_translation_does_not_block_the_stamp(self):
        _seed(_previous())
        _seed("", language="en")

        _run()

        assert _page().legal_text_revision == 1

    def test_stamps_a_page_already_on_current_text(self):
        _seed(_current())

        _run()

        assert _body() == _current()
        assert _page().legal_text_revision == 1

    def test_a_page_with_no_text_is_not_stamped(self):
        """Stamping an empty page would hide the update from whatever
        the merchant writes into it later."""
        ContentPage.objects.create(slug="privacy", is_published=True)

        _run()

        assert _page().legal_text_revision is None

    def test_skips_a_page_already_at_the_revision(self):
        own = "<p>Reviewed by the merchant.</p>"
        page = _seed(_previous())
        ContentPage.objects.filter(pk=page.pk).update(legal_text_revision=1)
        ContentPageTranslation.objects.filter(master=page).update(body=own)

        _run()

        assert _body() == own

    def test_skips_the_public_schema(self):
        _seed(_previous())

        _run(get_public_schema_name())

        assert _body() == _previous()
        assert _page().legal_text_revision is None

    def test_is_idempotent(self):
        _seed(_previous())

        _run()
        once = (_body(), _page().legal_text_revision)
        _run()

        assert (_body(), _page().legal_text_revision) == once

    def test_missing_page_is_not_created(self):
        _run()

        assert not ContentPage.objects.exists()


class TestMigrationCopiesMatchTheLiveText(TestCase):
    """The migration carries its own copies of both texts. The current
    one must be the live document, or new tenants (seeded by the live
    module) and migrated tenants would end up on different text."""

    def test_current_body_is_the_live_privacy_document(self):
        assert (
            migration_module.CURRENT_BODY == LEGAL_DOCUMENTS["privacy"]["body"]
        )

    def test_current_body_is_the_previous_one_plus_the_section(self):
        assert (
            migration_module.CURRENT_BODY
            == migration_module.PREVIOUS_BODY
            + PRIVACY_SERVER_LOGS_SECTION["el"]
        )
