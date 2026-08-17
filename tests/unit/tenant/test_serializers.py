from __future__ import annotations

from django.test import SimpleTestCase

from tenant.config import Tenant
from tenant.serializers import TenantConfigSerializer


def _make_tenant(**overrides) -> Tenant:
    defaults = dict(
        schema_name="webside",
        name="Webside",
        store_name="Webside",
        store_description="",
        logo_light_url="",
        logo_dark_url="",
        favicon_url="",
        primary_color="neutral",
        neutral_color="zinc",
        accent_hex="#003DFF",
        success_hex="#16a34a",
        warning_hex="#ca8a04",
        error_hex="#dc2626",
        info_hex="#2563eb",
        theme_preset="default",
        theme_metadata={},
        default_locale="el",
        default_currency="EUR",
        primary_domain="webside.gr",
        loyalty_enabled=True,
        blog_enabled=True,
        stripe_publishable_key="",
        allowed_csp_sources=[],
        meta_pixel_id="",
        tiktok_pixel_id="",
        ga_tracking_id="",
        totp_issuer="",
        turnstile_site_key="",
        socials_discord="",
        socials_facebook="",
        socials_instagram="",
        socials_pinterest="",
        socials_reddit="",
        socials_tiktok="",
        socials_twitter="",
        socials_youtube="",
        box_now_partner_id="",
        chat_api_key="",
    )
    defaults.update(overrides)
    return Tenant(**defaults)


class TenantConfigSerializerTests(SimpleTestCase):
    def test_serializes_all_public_fields(self):
        tenant = _make_tenant(
            store_description="A test store",
            box_now_partner_id="55",
            allowed_csp_sources=["https://example.com"],
            theme_metadata={"foo": "bar"},
        )

        data = TenantConfigSerializer(tenant).data

        self.assertEqual(data["schema_name"], "webside")
        self.assertEqual(data["name"], "Webside")
        self.assertEqual(data["store_name"], "Webside")
        self.assertEqual(data["store_description"], "A test store")
        self.assertEqual(data["primary_domain"], "webside.gr")
        self.assertEqual(data["box_now_partner_id"], "55")
        self.assertEqual(data["theme_metadata"], {"foo": "bar"})
        self.assertEqual(data["allowed_csp_sources"], ["https://example.com"])
        self.assertTrue(data["loyalty_enabled"])
        self.assertTrue(data["blog_enabled"])
        self.assertEqual(data["default_locale"], "el")
        self.assertEqual(data["default_currency"], "EUR")

    def test_primary_domain_reads_from_tenant_dataclass(self):
        tenant = _make_tenant(primary_domain="other.example")

        data = TenantConfigSerializer(tenant).data

        self.assertEqual(data["primary_domain"], "other.example")

    def test_excludes_admin_only_fields(self):
        """Secrets/billing fields on the multi-tenant branch's
        TenantAdminSerializer must never appear here."""
        tenant = _make_tenant()

        data = TenantConfigSerializer(tenant).data

        for admin_only_field in (
            "plan",
            "owner_email",
            "is_active",
            "paid_until",
            "viva_wallet_api_key",
            "viva_wallet_webhook_verification_key",
            "acs_api_key",
            "acs_company_password",
            "turnstile_secret_key",
            "meta_capi_access_token",
            "from_email",
            "contact_email",
            "chat_api_key",
        ):
            self.assertNotIn(admin_only_field, data)
