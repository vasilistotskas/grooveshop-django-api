"""Unit tests for tenant-scoped S3 storage classes (FIX 5).

The storage classes import ``connection`` lazily inside the location
property so that the active schema is read per-request (not once at
class-import time).  Tests patch ``django.db.connection`` directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _mock_connection(schema_name):
    conn = MagicMock()
    conn.schema_name = schema_name
    return conn


class TestTenantPublicMediaStorage:
    """TenantPublicMediaStorage.location should reflect the active schema."""

    def test_location_uses_schema_name(self):
        from core.storages import TenantPublicMediaStorage

        storage = TenantPublicMediaStorage.__new__(TenantPublicMediaStorage)
        with patch("django.db.connection", _mock_connection("webside")):
            assert storage.location == "media/webside"

    def test_location_fallback_when_no_schema(self):
        from core.storages import TenantPublicMediaStorage

        storage = TenantPublicMediaStorage.__new__(TenantPublicMediaStorage)
        mock_conn = MagicMock(spec=[])  # no schema_name attribute
        with patch("django.db.connection", mock_conn):
            assert storage.location == "media/public"

    def test_different_schemas_yield_different_locations(self):
        from core.storages import TenantPublicMediaStorage

        storage = TenantPublicMediaStorage.__new__(TenantPublicMediaStorage)

        with patch("django.db.connection", _mock_connection("tenant_a")):
            loc_a = storage.location
        with patch("django.db.connection", _mock_connection("tenant_b")):
            loc_b = storage.location

        assert loc_a == "media/tenant_a"
        assert loc_b == "media/tenant_b"
        assert loc_a != loc_b

    def test_acl_is_public_read(self):
        from core.storages import TenantPublicMediaStorage

        assert TenantPublicMediaStorage.default_acl == "public-read"


class TestTenantPrivateMediaStorage:
    """TenantPrivateMediaStorage.location should include /private suffix."""

    def test_location_includes_private_suffix(self):
        from core.storages import TenantPrivateMediaStorage

        storage = TenantPrivateMediaStorage.__new__(TenantPrivateMediaStorage)
        with patch("django.db.connection", _mock_connection("webside")):
            assert storage.location == "media/webside/private"

    def test_querystring_auth_is_enabled(self):
        from core.storages import TenantPrivateMediaStorage

        assert TenantPrivateMediaStorage.querystring_auth is True

    def test_acl_is_private(self):
        from core.storages import TenantPrivateMediaStorage

        assert TenantPrivateMediaStorage.default_acl == "private"
