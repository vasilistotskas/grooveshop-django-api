"""Exercises page_config/migrations/0007_seed_content_pages.py directly.

Data migrations aren't covered by the normal model/view test path, so
this loads the module (numeric-prefixed, hence importlib rather than a
plain import) and calls its RunPython callables against the live app
registry and the real test connection — matching the lightweight style
already used for ``page_config.defaults`` seed functions, since the
project has no dedicated migration-testing harness.
"""

import importlib

from django.apps import apps as live_apps
from django.db import connection
from django.test import TestCase
from django_tenants.utils import get_public_schema_name

from page_config.models import ContentPage

migration_module = importlib.import_module(
    "page_config.migrations.0007_seed_content_pages"
)


class _FakeConnection:
    """Exposes only what the RunPython callables read: schema_name +
    alias. Real reads/writes still go through the ambient test
    connection — only the reported schema name is overridden, so the
    public-schema guard can be tested deterministically regardless of
    which schema the test database itself happens to run under."""

    def __init__(self, schema_name: str):
        self.schema_name = schema_name
        self.alias = connection.alias


class _FakeSchemaEditor:
    def __init__(self, schema_name: str):
        self.connection = _FakeConnection(schema_name)


class TestSeedContentPagesMigration(TestCase):
    def test_creates_default_pages(self):
        migration_module.seed_content_pages(
            live_apps, _FakeSchemaEditor("some-tenant")
        )
        assert ContentPage.objects.count() == len(
            migration_module.DEFAULT_CONTENT_PAGES
        )
        assert ContentPage.objects.filter(slug="terms").exists()

    def test_created_pages_are_unpublished(self):
        migration_module.seed_content_pages(
            live_apps, _FakeSchemaEditor("some-tenant")
        )
        assert not ContentPage.objects.filter(is_published=True).exists()

    def test_idempotent(self):
        editor = _FakeSchemaEditor("some-tenant")
        migration_module.seed_content_pages(live_apps, editor)
        first_count = ContentPage.objects.count()

        migration_module.seed_content_pages(live_apps, editor)
        assert ContentPage.objects.count() == first_count

    def test_skips_public_schema(self):
        migration_module.seed_content_pages(
            live_apps, _FakeSchemaEditor(get_public_schema_name())
        )
        assert ContentPage.objects.count() == 0

    def test_reverse_removes_only_default_slugs(self):
        editor = _FakeSchemaEditor("some-tenant")
        migration_module.seed_content_pages(live_apps, editor)
        ContentPage.objects.create(slug="merchant-custom-page")

        migration_module.unseed_content_pages(live_apps, editor)

        assert ContentPage.objects.count() == 1
        assert ContentPage.objects.filter(slug="merchant-custom-page").exists()
