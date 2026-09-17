"""Exercises page_config/migrations/0022_correct_legal_document_identity.

0021 substituted ``{site_host}`` / ``{store_name}`` from
``connection.tenant``, which carries no ``domains`` while
``migrate_schemas`` runs — so every live tenant was seeded terms naming
``APP_MAIN_HOST_NAME`` under the platform's ``SITE_NAME``. 0022 rewrites
exactly those rows and nothing else.

The property that matters is the same one 0021 had: a merchant who has
edited the page keeps it. 0022 identifies its own output by
regenerating the fallback rendering and comparing byte for byte, so
anything else — a merchant's text, or a row already carrying the right
identity — is left alone.
"""

import importlib

from django.apps import apps as live_apps
from django.conf import settings
from django.db import connection
from django.test import TestCase
from django_tenants.utils import get_public_schema_name

from page_config.models import ContentPage, ContentPageTranslation

migration_module = importlib.import_module(
    "page_config.migrations.0022_correct_legal_document_identity"
)

LANGUAGE = settings.PARLER_DEFAULT_LANGUAGE_CODE
STALE_HOST = getattr(settings, "APP_MAIN_HOST_NAME", "") or ""
STALE_NAME = getattr(settings, "SITE_NAME", "") or ""


class _FakeConnection:
    """Reports a schema name; every read/write still goes through the
    ambient test connection, including the cursor the migration opens to
    resolve the tenant."""

    def __init__(self, schema_name: str):
        self.schema_name = schema_name
        self.alias = connection.alias

    def cursor(self):
        return connection.cursor()


class _FakeSchemaEditor:
    def __init__(self, schema_name: str):
        self.connection = _FakeConnection(schema_name)


SCHEMA = "a-real-tenant"
HOST = "a-real-tenant.example.gr"
STORE = "A Real Tenant"


def _make_tenant() -> None:
    """A tenant row the migration can actually resolve.

    Without one it falls back to the stale identity, returns before the
    loop, and every assertion below passes without executing the code it
    is meant to guard — which is exactly how the first version of this
    file mutation-tested green.
    """
    from tenant.models import Tenant, TenantDomain

    tenant = Tenant(schema_name=SCHEMA, name=STORE, store_name=STORE)
    # django-tenants CREATES A POSTGRES SCHEMA and runs every tenant
    # migration on save. This test only needs the row the migration reads
    # back, and paying for a schema took the file from 11s to 9 minutes.
    tenant.auto_create_schema = False
    tenant.save()
    TenantDomain.objects.create(tenant=tenant, domain=HOST, is_primary=True)


def _run(schema_name: str = SCHEMA) -> None:
    migration_module.correct_legal_documents(
        live_apps, _FakeSchemaEditor(schema_name)
    )


def _seed(slug: str, body: str, *, published: bool = True) -> ContentPage:
    page = ContentPage.objects.create(slug=slug, is_published=published)
    ContentPageTranslation.objects.create(
        master=page, language_code=LANGUAGE, title="T", body=body
    )
    return page


def _body(slug: str) -> str:
    return (
        ContentPage.objects.get(slug=slug)
        .translations.get(language_code=LANGUAGE)
        .body
    )


def _stale(slug: str) -> str:
    return migration_module._render(slug, STALE_HOST, STALE_NAME)


class TestCorrectLegalDocumentIdentity(TestCase):
    def setUp(self):
        _make_tenant()

    def test_rewrites_a_stale_row_with_the_tenants_own_identity(self):
        _seed("terms", _stale("terms"))

        _run()

        body = _body("terms")
        assert HOST in body
        assert STALE_HOST not in body

    def test_skips_the_public_schema(self):
        _seed("terms", _stale("terms"))

        _run(get_public_schema_name())

        assert _body("terms") == _stale("terms")

    def test_leaves_a_merchant_document_alone(self):
        """The property this migration must not violate."""
        own = "<p>Οι δικοί μας όροι, γραμμένοι από εμάς.</p>"
        _seed("terms", own)

        _run()

        assert _body("terms") == own

    def test_leaves_an_already_correct_document_alone(self):
        correct = migration_module._render(
            "terms", "example.gr", "Example Store"
        )
        _seed("terms", correct)

        _run()

        assert _body("terms") == correct

    def test_unknown_schema_changes_nothing(self):
        """No tenant row means no identity to apply — the stale text is
        still wrong, but inventing one would be worse."""
        _seed("terms", _stale("terms"))

        _run("no-such-schema")

        assert _body("terms") == _stale("terms")

    def test_leaves_the_other_documents_alone_when_only_one_is_stale(self):
        own = "<p>Δική μας πολιτική cookies.</p>"
        _seed("terms", _stale("terms"))
        _seed("cookies", own)

        _run()

        assert HOST in _body("terms")
        assert _body("cookies") == own

    def test_is_idempotent_on_a_stale_row(self):
        _seed("terms", _stale("terms"))

        _run()
        once = _body("terms")
        _run()

        assert _body("terms") == once

    def test_missing_page_is_not_created(self):
        _run()

        assert not ContentPage.objects.exists()

    def test_fallback_identity_matches_what_0021_used(self):
        """If these drift apart, 0022 stops recognising its own target
        and silently corrects nothing."""
        assert migration_module._fallback_identity() == (
            STALE_HOST,
            STALE_NAME,
        )
