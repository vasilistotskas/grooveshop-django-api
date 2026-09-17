"""Exercises page_config/migrations/0021_seed_legal_documents.py directly.

This migration is the one that put real legal text into four live
tenants, so the property that matters most is the one it must NEVER
violate: a merchant who has already written their own document keeps it,
untouched, including its published state. ``ekfyseosfyteias`` was in
exactly that position when the migration was written.

Same lightweight harness as ``test_migration_seed_content_pages``: the
module is numeric-prefixed so it loads via importlib, and the RunPython
callable runs against the live app registry and the real test
connection with only the reported schema name faked.
"""

import importlib

from django.apps import apps as live_apps
from django.conf import settings
from django.db import connection
from django.test import TestCase
from django_tenants.utils import get_public_schema_name

from page_config.models import ContentPage, ContentPageTranslation

migration_module = importlib.import_module(
    "page_config.migrations.0021_seed_legal_documents"
)

LANGUAGE = settings.PARLER_DEFAULT_LANGUAGE_CODE


class _FakeConnection:
    """Only what the callable reads: schema_name, alias and tenant.

    ``tenant`` is deliberately absent by default so the domain/name
    lookup exercises its fallback path — a tenant row is not available
    in a plain TestCase, and the migration must not require one.
    """

    def __init__(self, schema_name: str):
        self.schema_name = schema_name
        self.alias = connection.alias


class _FakeSchemaEditor:
    def __init__(self, schema_name: str):
        self.connection = _FakeConnection(schema_name)


def _run(schema_name: str = "some-tenant") -> None:
    migration_module.seed_legal_documents(
        live_apps, _FakeSchemaEditor(schema_name)
    )


def _body(slug: str) -> str:
    return (
        ContentPage.objects.get(slug=slug)
        .translations.get(language_code=LANGUAGE)
        .body
    )


class TestSeedLegalDocumentsMigration(TestCase):
    def test_creates_and_publishes_the_three_documents(self):
        _run()

        for slug in migration_module.LEGAL_DOCUMENTS:
            page = ContentPage.objects.get(slug=slug)
            assert page.is_published is True, slug
            assert "<section id=" in _body(slug), slug

    def test_skips_the_public_schema(self):
        _run(get_public_schema_name())

        assert not ContentPage.objects.exists()

    def test_replaces_an_untouched_placeholder(self):
        page = ContentPage.objects.create(slug="terms", is_published=False)
        ContentPageTranslation.objects.create(
            master=page,
            language_code=LANGUAGE,
            title="Όροι Χρήσης",
            body=migration_module.PLACEHOLDER_BODIES["terms"],
        )

        _run()

        page.refresh_from_db()
        assert page.is_published is True
        assert "<section id=" in _body("terms")

    def test_replaces_an_empty_body(self):
        page = ContentPage.objects.create(slug="privacy", is_published=False)
        ContentPageTranslation.objects.create(
            master=page, language_code=LANGUAGE, title="Π", body="   "
        )

        _run()

        assert "<section id=" in _body("privacy")

    def test_never_overwrites_a_merchant_document(self):
        """The property this migration exists to not violate."""
        own = "<p>Η δική μας πολιτική απορρήτου.</p>"
        page = ContentPage.objects.create(slug="privacy", is_published=True)
        ContentPageTranslation.objects.create(
            master=page,
            language_code=LANGUAGE,
            title="Δική μας",
            body=own,
        )

        _run()

        page.refresh_from_db()
        assert _body("privacy") == own
        assert page.is_published is True
        assert (
            ContentPage.objects.get(slug="privacy")
            .translations.get(language_code=LANGUAGE)
            .title
            == "Δική μας"
        )

    def test_leaves_an_unpublished_merchant_draft_unpublished(self):
        """A merchant mid-draft must not be published behind their back."""
        own = "<p>Δουλεύω ακόμη πάνω σε αυτό.</p>"
        page = ContentPage.objects.create(slug="cookies", is_published=False)
        ContentPageTranslation.objects.create(
            master=page, language_code=LANGUAGE, title="Πρόχειρο", body=own
        )

        _run()

        page.refresh_from_db()
        assert _body("cookies") == own
        assert page.is_published is False

    def test_is_idempotent(self):
        _run()
        first = {slug: _body(slug) for slug in migration_module.LEGAL_DOCUMENTS}

        _run()

        assert {
            slug: _body(slug) for slug in migration_module.LEGAL_DOCUMENTS
        } == first
        assert ContentPage.objects.count() == len(
            migration_module.LEGAL_DOCUMENTS
        )

    def test_substitutes_the_tenant_tokens(self):
        _run()

        for slug in migration_module.LEGAL_DOCUMENTS:
            body = _body(slug)
            assert "{site_host}" not in body, slug
            assert "{store_name}" not in body, slug
