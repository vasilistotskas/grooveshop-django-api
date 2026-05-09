"""Unit tests for tenant serializer correctness (FIX 9)."""

from __future__ import annotations


from tenant.serializers import TenantAdminSerializer, TenantConfigSerializer


class TestTenantConfigSerializer:
    """Verify the unauthenticated-facing serializer does NOT expose plan."""

    def test_plan_not_in_fields(self):
        serializer = TenantConfigSerializer()
        assert "plan" not in serializer.fields

    def test_expected_public_fields_present(self):
        serializer = TenantConfigSerializer()
        expected = {
            "schema_name",
            "name",
            "store_name",
            "store_description",
            "logo_light_url",
            "logo_dark_url",
            "favicon_url",
            "primary_color",
            "neutral_color",
            "accent_hex",
            "success_hex",
            "warning_hex",
            "error_hex",
            "info_hex",
            "theme_preset",
            "theme_metadata",
            "default_locale",
            "default_currency",
            "primary_domain",
            "loyalty_enabled",
            "blog_enabled",
        }
        assert expected.issubset(set(serializer.fields.keys()))


class TestTenantAdminSerializer:
    """Platform-admin serializer DOES include plan (full info)."""

    def test_plan_in_admin_fields(self):
        serializer = TenantAdminSerializer()
        assert "plan" in serializer.fields

    def test_stripe_connect_id_in_admin_fields(self):
        serializer = TenantAdminSerializer()
        assert "stripe_connect_account_id" in serializer.fields
