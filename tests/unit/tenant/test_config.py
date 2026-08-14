from __future__ import annotations

from django.test import SimpleTestCase, override_settings

from tenant.config import Tenant, get_tenant_config, resolve_tenant_domains

_TENANT_OVERRIDES = {
    "TENANT_SCHEMA_NAME": "webside",
    "TENANT_PRIMARY_DOMAIN": "webside.gr",
    "TENANT_EXTRA_DOMAINS": ["www.webside.gr"],
    "TENANT_STORE_DESCRIPTION": "",
    "TENANT_LOGO_LIGHT_URL": "",
    "TENANT_LOGO_DARK_URL": "",
    "TENANT_FAVICON_URL": "",
    "TENANT_PRIMARY_COLOR": "neutral",
    "TENANT_NEUTRAL_COLOR": "zinc",
    "TENANT_ACCENT_HEX": "#003DFF",
    "TENANT_SUCCESS_HEX": "#16a34a",
    "TENANT_WARNING_HEX": "#ca8a04",
    "TENANT_ERROR_HEX": "#dc2626",
    "TENANT_INFO_HEX": "#2563eb",
    "TENANT_THEME_PRESET": "default",
    "TENANT_THEME_METADATA": {},
    "TENANT_LOYALTY_ENABLED": True,
    "TENANT_BLOG_ENABLED": True,
    "TENANT_STRIPE_PUBLISHABLE_KEY": "",
    "TENANT_ALLOWED_CSP_SOURCES": [],
    "TENANT_TIKTOK_PIXEL_ID": "",
    "TENANT_GA_TRACKING_ID": "",
    "TENANT_TOTP_ISSUER": "",
    "TENANT_TURNSTILE_SITE_KEY": "",
    "TENANT_SOCIALS_DISCORD": "",
    "TENANT_SOCIALS_FACEBOOK": "",
    "TENANT_SOCIALS_INSTAGRAM": "",
    "TENANT_SOCIALS_PINTEREST": "",
    "TENANT_SOCIALS_REDDIT": "",
    "TENANT_SOCIALS_TIKTOK": "",
    "TENANT_SOCIALS_TWITTER": "",
    "TENANT_SOCIALS_YOUTUBE": "",
    "SITE_NAME": "Webside",
    "LANGUAGE_CODE": "el",
    "DEFAULT_CURRENCY": "EUR",
    "BOXNOW_PARTNER_ID": "12345",
    "META_PIXEL_ID": "987654321",
}


@override_settings(**_TENANT_OVERRIDES)
class GetTenantConfigTests(SimpleTestCase):
    def test_returns_tenant_dataclass_sourced_from_settings(self):
        config = get_tenant_config()

        self.assertIsInstance(config, Tenant)
        self.assertEqual(config.schema_name, "webside")
        self.assertEqual(config.name, "Webside")
        self.assertEqual(config.store_name, "Webside")
        self.assertEqual(config.default_locale, "el")
        self.assertEqual(config.default_currency, "EUR")
        self.assertEqual(config.primary_domain, "webside.gr")
        self.assertTrue(config.loyalty_enabled)
        self.assertTrue(config.blog_enabled)
        self.assertEqual(config.box_now_partner_id, "12345")
        self.assertEqual(config.meta_pixel_id, "987654321")
        self.assertEqual(config.accent_hex, "#003DFF")
        self.assertEqual(config.theme_metadata, {})
        self.assertEqual(config.allowed_csp_sources, [])

    def test_reuses_existing_settings_instead_of_duplicating(self):
        """name/store_name track SITE_NAME directly (no separate
        TENANT_NAME/TENANT_STORE_NAME setting shadowing it); same for
        default_locale/default_currency/box_now_partner_id/meta_pixel_id
        tracking LANGUAGE_CODE/DEFAULT_CURRENCY/BOXNOW_PARTNER_ID/
        META_PIXEL_ID.
        """
        with override_settings(
            SITE_NAME="Different Store",
            LANGUAGE_CODE="en",
            DEFAULT_CURRENCY="USD",
            BOXNOW_PARTNER_ID="99",
            META_PIXEL_ID="111",
        ):
            config = get_tenant_config()

        self.assertEqual(config.name, "Different Store")
        self.assertEqual(config.store_name, "Different Store")
        self.assertEqual(config.default_locale, "en")
        self.assertEqual(config.default_currency, "USD")
        self.assertEqual(config.box_now_partner_id, "99")
        self.assertEqual(config.meta_pixel_id, "111")


@override_settings(**_TENANT_OVERRIDES)
class ResolveTenantDomainsTests(SimpleTestCase):
    def test_includes_primary_and_extra_domains(self):
        self.assertEqual(
            resolve_tenant_domains(), {"webside.gr", "www.webside.gr"}
        )

    def test_unrelated_domain_is_excluded(self):
        self.assertNotIn("not-a-store.example", resolve_tenant_domains())
