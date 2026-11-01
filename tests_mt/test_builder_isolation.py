"""The page builder is schema-bound end to end.

``builder`` is TENANT_APPS-only, so its tables do not exist in public at
all: a document written for one store cannot be read by another because the
query itself fails outside that store's schema. Release numbers count per
store, the published page is cached under the schema, and the lock that
serialises a store's releases is per schema.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.core.cache import cache
from django.db import transaction
from django.db.utils import ProgrammingError
from django_tenants.utils import schema_context

from builder import published, services
from builder.storefront import clear_catalogue_cache
from tenant.auth_backends import PLATFORM_IDENTITY_ATTR
from tests.utils.builder import FakeCatalogue, document, section

HOME = "template:home"


class _Staff:
    """A staff member who may do everything, as a superuser does."""

    pk = 1

    def has_perm(self, permission: str) -> bool:
        return True


setattr(_Staff, PLATFORM_IDENTITY_ATTR, True)


@pytest.fixture(autouse=True)
def _storefront(settings):
    settings.NUXT_INTERNAL_BASE_URL = "http://storefront.test"
    clear_catalogue_cache()
    fake = FakeCatalogue()
    with (
        mock.patch("builder.storefront.requests.get", side_effect=fake.get),
        mock.patch("builder.services.feature_on", return_value=True),
    ):
        yield
    clear_catalogue_cache()


def _publish(text: str) -> None:
    staff = _Staff()
    services.save_draft(
        staff,
        key=HOME,
        draft=document(("b", section("banner@1", {"text": text}))),
        i18n={},
        expected_revision=0,
    )
    services.publish(staff, keys=[HOME])


@pytest.mark.django_db
def test_the_tables_are_schema_local(mt_tenant):
    from builder.models import BuilderDocument

    with schema_context(mt_tenant.schema_name):
        assert BuilderDocument.objects.count() == 0

    with schema_context("public"):
        with (
            pytest.raises(ProgrammingError, match="does not exist"),
            transaction.atomic(),
        ):
            BuilderDocument.objects.exists()


@pytest.mark.django_db
def test_two_stores_publish_independently(mt_tenant, mt_tenant_b):
    cache.clear()
    with schema_context(mt_tenant.schema_name):
        _publish("store a")
    with schema_context(mt_tenant_b.schema_name):
        assert published.published_page("home", "el") is None, (
            "store B served what only store A published"
        )
        _publish("store b")

    with schema_context(mt_tenant.schema_name):
        page = published.published_page("home", "el")
    assert page is not None
    assert page["release"] == 1
    assert page["page"]["sections"]["b"]["settings"]["text"] == "store a"

    with schema_context(mt_tenant_b.schema_name):
        page = published.published_page("home", "el")
    assert page is not None
    assert page["release"] == 1, "release numbers count per store"
    assert page["page"]["sections"]["b"]["settings"]["text"] == "store b"


@pytest.mark.django_db
def test_the_release_lock_is_taken_per_schema(mt_tenant, mt_tenant_b):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    seen = []
    for tenant in (mt_tenant, mt_tenant_b):
        with schema_context(tenant.schema_name):
            with CaptureQueriesContext(connection) as queries:
                services._lock_release_sequence()
            seen.append(
                next(
                    q["sql"]
                    for q in queries
                    if "pg_advisory_xact_lock" in q["sql"]
                )
            )

    assert mt_tenant.schema_name in seen[0]
    assert mt_tenant_b.schema_name in seen[1]
    assert seen[0] != seen[1]


@pytest.mark.django_db
def test_an_upload_lands_in_its_stores_media_directory(mt_tenant, mt_tenant_b):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    from builder import media_services

    def png() -> SimpleUploadedFile:
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4)).save(buffer, format="PNG")
        return SimpleUploadedFile("a.png", buffer.getvalue(), "image/png")

    paths = []
    for tenant in (mt_tenant, mt_tenant_b):
        with schema_context(tenant.schema_name):
            asset = media_services.create_asset(_Staff(), upload=png())
            paths.append((tenant.schema_name, asset.file.path, asset.file.url))

    for schema, path, url in paths:
        assert f"{schema}" in path.replace("\\", "/").split("/")
        assert f"/{schema}/uploads/builder/" in url
