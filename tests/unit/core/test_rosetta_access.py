"""Tests for ``core.rosetta_access``.

``core.models.Translation`` is a SHARED (public-schema-only) model —
Rosetta edits patch the ONE platform-wide gettext overlay served to
every tenant. Without the schema allowlist here, a tenant-schema
superuser could reach ``/rosetta/...`` on their own tenant host
(django-tenants resolves it there — Rosetta has no schema awareness of
its own) and silently rewrite copy for every other tenant.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.rosetta_access import (
    _allowed_schemas,
    rosetta_schema_allowed,
    tenant_scoped_rosetta_access,
)


def _superuser():
    user = MagicMock()
    user.is_authenticated = True
    user.is_superuser = True
    user.is_staff = True
    return user


class TestAllowedSchemas:
    def test_default_allows_only_public(self, settings):
        settings.ROSETTA_ALLOWED_SCHEMAS = "public"
        assert _allowed_schemas() == {"public"}

    def test_comma_separated_allowlist(self, settings):
        settings.ROSETTA_ALLOWED_SCHEMAS = "public, webside"
        assert _allowed_schemas() == {"public", "webside"}

    def test_falls_back_to_public_schema_name_when_unset(self, settings):
        settings.ROSETTA_ALLOWED_SCHEMAS = ""
        assert _allowed_schemas() == {"public"}


class TestRosettaSchemaAllowed:
    def test_allowed_on_public(self, settings):
        settings.ROSETTA_ALLOWED_SCHEMAS = "public"
        with patch("core.rosetta_access.connection") as conn:
            conn.schema_name = "public"
            assert rosetta_schema_allowed() is True

    def test_denied_on_tenant_schema_by_default(self, settings):
        settings.ROSETTA_ALLOWED_SCHEMAS = "public"
        with patch("core.rosetta_access.connection") as conn:
            conn.schema_name = "some_tenant"
            assert rosetta_schema_allowed() is False

    def test_allowed_when_tenant_schema_explicitly_allowlisted(self, settings):
        settings.ROSETTA_ALLOWED_SCHEMAS = "public,webside"
        with patch("core.rosetta_access.connection") as conn:
            conn.schema_name = "webside"
            assert rosetta_schema_allowed() is True


class TestTenantScopedRosettaAccess:
    """The default (env-driven) allowlist is ``"public"`` — a
    tenant-schema superuser is denied unless their schema is
    explicitly opted in, regardless of how privileged they are within
    their own tenant.
    """

    def test_denies_tenant_schema_superuser_when_not_allowlisted(
        self, settings
    ):
        settings.ROSETTA_ALLOWED_SCHEMAS = "public"
        user = _superuser()
        with patch("core.rosetta_access.connection") as conn:
            conn.schema_name = "some_tenant"
            assert tenant_scoped_rosetta_access(user) is False

    def test_allows_superuser_on_public_schema(self, settings):
        settings.ROSETTA_ALLOWED_SCHEMAS = "public"
        user = _superuser()
        with patch("core.rosetta_access.connection") as conn:
            conn.schema_name = "public"
            assert tenant_scoped_rosetta_access(user) is True

    def test_denies_non_superuser_even_on_public_schema(self, settings):
        settings.ROSETTA_ALLOWED_SCHEMAS = "public"
        user = MagicMock()
        user.is_authenticated = True
        user.is_superuser = False
        user.is_staff = False
        user.groups.filter.return_value.exists.return_value = False
        with patch("core.rosetta_access.connection") as conn:
            conn.schema_name = "public"
            assert tenant_scoped_rosetta_access(user) is False

    def test_allowlisted_tenant_schema_superuser_allowed(self, settings):
        settings.ROSETTA_ALLOWED_SCHEMAS = "public,webside"
        user = _superuser()
        with patch("core.rosetta_access.connection") as conn:
            conn.schema_name = "webside"
            assert tenant_scoped_rosetta_access(user) is True

    def test_unauthenticated_denied_regardless_of_schema(self, settings):
        settings.ROSETTA_ALLOWED_SCHEMAS = "public"
        user = MagicMock()
        user.is_authenticated = False
        with patch("core.rosetta_access.connection") as conn:
            conn.schema_name = "public"
            assert tenant_scoped_rosetta_access(user) is False


class TestTranslationVersionKeyIsSchemaIndependent:
    def test_key_uses_global_prefix(self):
        from core.rosetta_storage import TRANSLATION_VERSION_CACHE_KEY

        assert TRANSLATION_VERSION_CACHE_KEY.startswith("global:")

    @pytest.mark.django_db
    def test_version_tick_propagates_across_schemas(self):
        """Written while resolving in one schema, must be readable from
        a totally different schema — this is what lets
        ``TranslationReloadMiddleware`` (other tenants' pods) and the
        Celery ``task_prerun`` handler notice a Rosetta save that
        landed on a different schema.
        """
        from django.core.cache import cache

        from core.rosetta_storage import TRANSLATION_VERSION_CACHE_KEY

        with patch("tenant.cache.connection") as conn:
            conn.schema_name = "some_tenant"
            cache.set(TRANSLATION_VERSION_CACHE_KEY, 123.456, timeout=None)

        with patch("tenant.cache.connection") as conn:
            conn.schema_name = "a_completely_different_tenant"
            assert cache.get(TRANSLATION_VERSION_CACHE_KEY) == 123.456

        # Cleanup — avoid leaking into other tests reading this key.
        with patch("tenant.cache.connection") as conn:
            conn.schema_name = "public"
            cache.delete(TRANSLATION_VERSION_CACHE_KEY)
