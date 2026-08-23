"""Invoice PDFs must be partitioned by tenant schema at ACCESS time.

``FileField(storage=callable)`` evaluates the callable once, at model-
class definition, when no tenant is bound — so a storage that captured
the schema eagerly would freeze every tenant's invoices under
``public``. The storage's location must be resolved per-operation.
"""

from __future__ import annotations

from unittest import mock

from django.db import connection

from order.models.invoice import (
    Invoice,
    TenantPrivateInvoiceStorage,
    _private_media_root,
)


def _schema_segment(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


class TestTenantPrivateInvoiceStorage:
    def test_location_tracks_the_active_schema(self):
        storage = TenantPrivateInvoiceStorage()
        with mock.patch.object(connection, "schema_name", "acme"):
            assert _schema_segment(storage.location) == "acme"
            assert _schema_segment(storage.base_location) == "acme"
        with mock.patch.object(connection, "schema_name", "beta"):
            assert _schema_segment(storage.location) == "beta"

    def test_two_schemas_get_distinct_locations(self):
        storage = TenantPrivateInvoiceStorage()
        with mock.patch.object(connection, "schema_name", "acme"):
            acme = storage.location
        with mock.patch.object(connection, "schema_name", "beta"):
            beta = storage.location
        assert acme != beta

    def test_location_lives_under_the_private_root(self):
        storage = TenantPrivateInvoiceStorage()
        with mock.patch.object(connection, "schema_name", "acme"):
            assert storage.location.startswith(_private_media_root())


class TestInvoiceFieldStorage:
    def test_field_uses_the_dynamic_storage(self):
        """The FileField resolved the callable to the per-tenant class,
        not a location-frozen FileSystemStorage."""
        storage = Invoice._meta.get_field("document_file").storage
        assert isinstance(storage, TenantPrivateInvoiceStorage)

    def test_field_storage_is_not_frozen_to_public(self):
        storage = Invoice._meta.get_field("document_file").storage
        with mock.patch.object(connection, "schema_name", "webside"):
            assert _schema_segment(storage.location) == "webside"
