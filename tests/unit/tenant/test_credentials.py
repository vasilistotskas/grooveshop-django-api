"""Unit tests for tenant/credentials.py helpers.

Two contracts are covered here:

1. **Fallback helpers** (email, site name, MFA TOTP issuer):
   - Tenant field value is returned when set (non-empty).
   - Falls back to the settings value when the tenant field is empty.
   - Falls back to the settings value when connection.tenant is None.
   - Returns "" when both are absent.

2. **Third-party credential helpers** (Stripe, Viva Wallet, ACS,
   BoxNow, Meta CAPI/Pixel) — NO fallback:
   - Tenant field value is returned when set (non-empty).
   - Returns "" (or False for the nullable ``live_mode`` flag) when
     the tenant field is empty or there is no active tenant — even
     when the matching settings value IS populated (assert no
     leakage via ``override_settings``/the ``settings`` fixture).

- Greek strings (ACS billing codes) pass through unchanged.
- Phase 2B: email, MFA TOTP issuer, and Meta CAPI helpers.
"""

from __future__ import annotations

import pytest
from django.db import connection

from tenant.credentials import (
    _get_tenant_field,
    acs_credentials,
    box_now_credentials,
    tenant_contact_email,
    tenant_from_email,
    tenant_meta_capi_access_token,
    tenant_meta_capi_dataset_id,
    tenant_meta_pixel_id,
    tenant_site_name,
    tenant_totp_issuer,
    viva_wallet_credentials,
)


# ---------------------------------------------------------------------------
# _get_tenant_field
# ---------------------------------------------------------------------------


class TestGetTenantField:
    """Tests for the underlying single-field resolver."""

    def test_returns_tenant_value_when_set(
        self, bind_tenant, tenant_factory, settings
    ):
        tenant = tenant_factory("cred-tenant-1")
        tenant.viva_wallet_api_key = "TENANT_API_KEY"
        tenant.save()
        bind_tenant(tenant)

        result = _get_tenant_field("viva_wallet_api_key", "VIVA_WALLET_API_KEY")
        assert result == "TENANT_API_KEY"

    def test_falls_back_to_settings_when_tenant_field_empty(
        self, bind_tenant, tenant_factory, settings
    ):
        tenant = tenant_factory("cred-tenant-2")
        tenant.viva_wallet_api_key = ""
        tenant.save()
        bind_tenant(tenant)
        settings.VIVA_WALLET_API_KEY = "SETTINGS_API_KEY"

        result = _get_tenant_field("viva_wallet_api_key", "VIVA_WALLET_API_KEY")
        assert result == "SETTINGS_API_KEY"

    def test_falls_back_to_settings_when_no_tenant(self, monkeypatch, settings):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.VIVA_WALLET_API_KEY = "ENV_API_KEY"

        result = _get_tenant_field("viva_wallet_api_key", "VIVA_WALLET_API_KEY")
        assert result == "ENV_API_KEY"

    def test_returns_empty_string_when_both_absent(self, monkeypatch, settings):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        if hasattr(settings, "VIVA_WALLET_API_KEY"):
            del settings.VIVA_WALLET_API_KEY

        result = _get_tenant_field("viva_wallet_api_key", "VIVA_WALLET_API_KEY")
        assert result == ""

    def test_no_fallback_setting_returns_empty(self, monkeypatch):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        result = _get_tenant_field("viva_wallet_api_key", None)
        assert result == ""

    def test_tenant_value_takes_priority_over_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        tenant = tenant_factory("cred-tenant-prio")
        tenant.viva_wallet_api_key = "TENANT_WINS"
        tenant.save()
        bind_tenant(tenant)
        settings.VIVA_WALLET_API_KEY = "SETTINGS_IGNORED"

        result = _get_tenant_field("viva_wallet_api_key", "VIVA_WALLET_API_KEY")
        assert result == "TENANT_WINS"

    def test_greek_billing_code_passes_through_unchanged(
        self, bind_tenant, tenant_factory
    ):
        """ACS billing codes contain Greek characters — must not be mangled."""
        tenant = tenant_factory("cred-tenant-greek")
        tenant.acs_billing_code = "2ΑΚ89587"
        tenant.save()
        bind_tenant(tenant)

        result = _get_tenant_field("acs_billing_code", "ACS_BILLING_CODE")
        assert result == "2ΑΚ89587"


# ---------------------------------------------------------------------------
# viva_wallet_credentials()
# ---------------------------------------------------------------------------


class TestVivaWalletCredentials:
    """No-fallback contract: tenant-only, settings are NEVER consulted."""

    def test_returns_empty_when_no_active_tenant_even_if_settings_set(
        self, monkeypatch, settings
    ):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.VIVA_WALLET_MERCHANT_ID = "m1"
        settings.VIVA_WALLET_API_KEY = "k1"
        settings.VIVA_WALLET_CLIENT_ID = "c1"
        settings.VIVA_WALLET_CLIENT_SECRET = "s1"
        settings.VIVA_WALLET_WEBHOOK_VERIFICATION_KEY = "v1"
        settings.VIVA_WALLET_SOURCE_CODE = "s1"
        settings.VIVA_WALLET_LIVE_MODE = True

        creds = viva_wallet_credentials()
        assert creds["merchant_id"] == ""
        assert creds["api_key"] == ""
        assert creds["client_id"] == ""
        assert creds["client_secret"] == ""
        assert creds["webhook_verification_key"] == ""
        assert creds["source_code"] == ""
        assert creds["live_mode"] is False

    def test_returns_tenant_values_when_set(self, bind_tenant, tenant_factory):
        tenant = tenant_factory("viva-tenant")
        tenant.viva_wallet_merchant_id = "TENANT_MID"
        tenant.viva_wallet_api_key = "TENANT_APIKEY"
        tenant.viva_wallet_client_id = "TENANT_CID"
        tenant.viva_wallet_client_secret = "TENANT_CSECRET"
        tenant.viva_wallet_webhook_verification_key = "TENANT_VK"
        tenant.viva_wallet_source_code = "TENANT_SRC"
        tenant.viva_wallet_live_mode = True
        tenant.save()
        bind_tenant(tenant)

        creds = viva_wallet_credentials()
        assert creds["merchant_id"] == "TENANT_MID"
        assert creds["api_key"] == "TENANT_APIKEY"
        assert creds["client_id"] == "TENANT_CID"
        assert creds["client_secret"] == "TENANT_CSECRET"
        assert creds["webhook_verification_key"] == "TENANT_VK"
        assert creds["source_code"] == "TENANT_SRC"
        assert creds["live_mode"] is True

    def test_empty_tenant_fields_stay_empty_ignoring_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        """Settings are populated but must NEVER be consulted (no leakage)."""
        tenant = tenant_factory("viva-empty")
        # All credential fields default to "" / None — don't override
        bind_tenant(tenant)
        settings.VIVA_WALLET_MERCHANT_ID = "SHOULD_NOT_BE_USED"
        settings.VIVA_WALLET_API_KEY = "SHOULD_NOT_BE_USED"
        settings.VIVA_WALLET_CLIENT_ID = "SHOULD_NOT_BE_USED"
        settings.VIVA_WALLET_CLIENT_SECRET = "SHOULD_NOT_BE_USED"
        settings.VIVA_WALLET_WEBHOOK_VERIFICATION_KEY = "SHOULD_NOT_BE_USED"
        settings.VIVA_WALLET_SOURCE_CODE = "SHOULD_NOT_BE_USED"
        settings.VIVA_WALLET_LIVE_MODE = True

        creds = viva_wallet_credentials()
        assert creds["merchant_id"] == ""
        assert creds["api_key"] == ""
        assert creds["client_id"] == ""
        assert creds["client_secret"] == ""
        assert creds["webhook_verification_key"] == ""
        assert creds["source_code"] == ""
        # Unset (None) nullable field → False, never "ask settings".
        assert creds["live_mode"] is False

    def test_partial_tenant_fields_do_not_leak_from_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        """Tenant sets merchant_id but not api_key — api_key stays empty,
        it does NOT fall back to settings on a per-field basis either."""
        tenant = tenant_factory("viva-partial")
        tenant.viva_wallet_merchant_id = "TENANT_MID"
        tenant.viva_wallet_api_key = ""
        tenant.save()
        bind_tenant(tenant)
        settings.VIVA_WALLET_API_KEY = "SHOULD_NOT_BE_USED"

        creds = viva_wallet_credentials()
        assert creds["merchant_id"] == "TENANT_MID"
        assert creds["api_key"] == ""

    def test_live_mode_true_is_a_plain_tenant_flag(
        self, bind_tenant, tenant_factory, settings
    ):
        settings.VIVA_WALLET_LIVE_MODE = False
        tenant = tenant_factory("viva-live-mode")
        tenant.viva_wallet_live_mode = True
        tenant.save(update_fields=["viva_wallet_live_mode"])
        bind_tenant(tenant)

        assert viva_wallet_credentials()["live_mode"] is True

    def test_live_mode_unset_resolves_to_false_ignoring_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        settings.VIVA_WALLET_LIVE_MODE = True
        tenant = tenant_factory("viva-live-mode-unset")
        bind_tenant(tenant)

        assert viva_wallet_credentials()["live_mode"] is False


# ---------------------------------------------------------------------------
# acs_credentials()
# ---------------------------------------------------------------------------


class TestAcsCredentials:
    """No-fallback contract: tenant-only, settings are NEVER consulted."""

    def test_returns_empty_when_no_active_tenant_even_if_settings_set(
        self, monkeypatch, settings
    ):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.ACS_API_KEY = "AKEY"
        settings.ACS_COMPANY_ID = "ACID"
        settings.ACS_COMPANY_PASSWORD = "ACPW"
        settings.ACS_USER_ID = "AUID"
        settings.ACS_USER_PASSWORD = "AUPW"
        settings.ACS_BILLING_CODE = "2ΑΚ89587"

        creds = acs_credentials()
        assert creds["api_key"] == ""
        assert creds["company_id"] == ""
        assert creds["company_password"] == ""
        assert creds["user_id"] == ""
        assert creds["user_password"] == ""
        assert creds["billing_code"] == ""
        assert creds["station_origin"] == ""

    def test_returns_tenant_values_when_set(self, bind_tenant, tenant_factory):
        tenant = tenant_factory("acs-tenant")
        tenant.acs_api_key = "T_AKEY"
        tenant.acs_company_id = "T_ACID"
        tenant.acs_company_password = "T_ACPW"
        tenant.acs_user_id = "T_AUID"
        tenant.acs_user_password = "T_AUPW"
        tenant.acs_billing_code = "9ΓΣ11111"
        tenant.acs_station_origin = "ΓΣ"
        tenant.save()
        bind_tenant(tenant)

        creds = acs_credentials()
        assert creds["api_key"] == "T_AKEY"
        assert creds["company_id"] == "T_ACID"
        assert creds["company_password"] == "T_ACPW"
        assert creds["user_id"] == "T_AUID"
        assert creds["user_password"] == "T_AUPW"
        assert creds["billing_code"] == "9ΓΣ11111"
        assert creds["station_origin"] == "ΓΣ"

    def test_greek_billing_code_preserved(self, bind_tenant, tenant_factory):
        tenant = tenant_factory("acs-greek")
        tenant.acs_billing_code = "2ΑΚ89587"
        tenant.save()
        bind_tenant(tenant)

        creds = acs_credentials()
        assert creds["billing_code"] == "2ΑΚ89587"

    def test_empty_tenant_fields_stay_empty_ignoring_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        """Settings are populated but must NEVER be consulted (no leakage)."""
        tenant = tenant_factory("webside-acs")
        # All fields default to ""
        bind_tenant(tenant)
        settings.ACS_API_KEY = "SHOULD_NOT_BE_USED"
        settings.ACS_COMPANY_ID = "SHOULD_NOT_BE_USED"
        settings.ACS_COMPANY_PASSWORD = "SHOULD_NOT_BE_USED"
        settings.ACS_USER_ID = "SHOULD_NOT_BE_USED"
        settings.ACS_USER_PASSWORD = "SHOULD_NOT_BE_USED"
        settings.ACS_BILLING_CODE = "SHOULD_NOT_BE_USED"

        creds = acs_credentials()
        assert creds["api_key"] == ""
        assert creds["company_id"] == ""
        assert creds["company_password"] == ""
        assert creds["user_id"] == ""
        assert creds["user_password"] == ""
        assert creds["billing_code"] == ""


# ---------------------------------------------------------------------------
# box_now_credentials()
# ---------------------------------------------------------------------------


class TestBoxNowCredentials:
    """No-fallback contract: tenant-only, settings are NEVER consulted."""

    def test_returns_empty_when_no_active_tenant_even_if_settings_set(
        self, monkeypatch, settings
    ):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.BOXNOW_CLIENT_ID = "BCL"
        settings.BOXNOW_CLIENT_SECRET = "BCS"
        settings.BOXNOW_PARTNER_ID = "999"
        settings.BOXNOW_WAREHOUSE_ID = "2"
        settings.BOXNOW_NOTIFY_PHONE = "+30210000001"
        settings.BOXNOW_WEBHOOK_SECRET = "WHS"

        creds = box_now_credentials()
        assert creds["client_id"] == ""
        assert creds["client_secret"] == ""
        assert creds["partner_id"] == ""
        assert creds["warehouse_id"] == ""
        assert creds["notify_phone"] == ""
        assert creds["webhook_secret"] == ""

    def test_returns_tenant_values_when_set(self, bind_tenant, tenant_factory):
        tenant = tenant_factory("bn-tenant")
        tenant.box_now_client_id = "T_BCL"
        tenant.box_now_client_secret = "T_BCS"
        tenant.box_now_partner_id = "12345"
        tenant.box_now_warehouse_id = "7"
        tenant.box_now_notify_phone = "+30690000001"
        tenant.box_now_webhook_secret = "T_WHS"
        tenant.save()
        bind_tenant(tenant)

        creds = box_now_credentials()
        assert creds["client_id"] == "T_BCL"
        assert creds["client_secret"] == "T_BCS"
        assert creds["partner_id"] == "12345"
        assert creds["warehouse_id"] == "7"
        assert creds["notify_phone"] == "+30690000001"
        assert creds["webhook_secret"] == "T_WHS"

    def test_webhook_secret_has_no_settings_fallback(
        self, bind_tenant, tenant_factory, settings
    ):
        """Each tenant holds its own BoxNow contract — no shared platform
        webhook secret."""
        tenant = tenant_factory("bn-whs")
        bind_tenant(tenant)
        settings.BOXNOW_WEBHOOK_SECRET = "SHOULD_NOT_BE_USED"

        creds = box_now_credentials()
        assert creds["webhook_secret"] == ""

    def test_empty_tenant_fields_stay_empty_ignoring_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        """Settings are populated but must NEVER be consulted (no leakage)."""
        tenant = tenant_factory("webside-bn")
        # All credential fields default to ""
        bind_tenant(tenant)
        settings.BOXNOW_CLIENT_ID = "SHOULD_NOT_BE_USED"
        settings.BOXNOW_CLIENT_SECRET = "SHOULD_NOT_BE_USED"
        settings.BOXNOW_PARTNER_ID = "SHOULD_NOT_BE_USED"
        settings.BOXNOW_WAREHOUSE_ID = "SHOULD_NOT_BE_USED"
        settings.BOXNOW_NOTIFY_PHONE = "SHOULD_NOT_BE_USED"

        creds = box_now_credentials()
        assert creds["client_id"] == ""
        assert creds["client_secret"] == ""
        assert creds["partner_id"] == ""
        assert creds["warehouse_id"] == ""
        assert creds["notify_phone"] == ""


# ---------------------------------------------------------------------------
# Phase 2B: Email helpers
# ---------------------------------------------------------------------------


class TestTenantFromEmail:
    def test_returns_tenant_value(self, bind_tenant, tenant_factory, settings):
        tenant = tenant_factory("email-from-1")
        tenant.from_email = "shop@brand.com"
        tenant.save()
        bind_tenant(tenant)
        assert tenant_from_email() == "shop@brand.com"

    def test_falls_back_to_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        tenant = tenant_factory("email-from-2")
        tenant.from_email = ""
        tenant.save()
        bind_tenant(tenant)
        settings.DEFAULT_FROM_EMAIL = "noreply@platform.com"
        assert tenant_from_email() == "noreply@platform.com"

    def test_no_tenant_uses_settings(self, monkeypatch, settings):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.DEFAULT_FROM_EMAIL = "platform@example.com"
        assert tenant_from_email() == "platform@example.com"

    def test_webside_safety_empty_tenant_field(
        self, bind_tenant, tenant_factory, settings
    ):
        """webside.gr: Tenant.from_email is empty → falls back cleanly."""
        tenant = tenant_factory("ws-from-email")
        bind_tenant(tenant)
        settings.DEFAULT_FROM_EMAIL = "noreply@webside.gr"
        assert tenant_from_email() == "noreply@webside.gr"


class TestTenantContactEmail:
    def test_returns_tenant_contact_email_first(
        self, bind_tenant, tenant_factory
    ):
        tenant = tenant_factory("contact-email-1")
        tenant.contact_email = "contact@shop.com"
        tenant.save()
        bind_tenant(tenant)
        assert tenant_contact_email() == "contact@shop.com"

    def test_falls_back_to_extra_setting(self, bind_tenant, tenant_factory, db):
        """When tenant.contact_email is empty reads CONTACT_EMAIL extra_setting."""
        from extra_settings.models import Setting

        tenant = tenant_factory("contact-email-2")
        tenant.contact_email = ""
        tenant.save()
        bind_tenant(tenant)

        Setting.objects.update_or_create(
            name="CONTACT_EMAIL",
            defaults={"value": "extra@example.com", "value_type": "string"},
        )
        assert tenant_contact_email() == "extra@example.com"

    def test_falls_back_to_info_email_when_extra_setting_empty(
        self, bind_tenant, tenant_factory, settings, db
    ):
        from extra_settings.models import Setting

        tenant = tenant_factory("contact-email-3")
        tenant.contact_email = ""
        tenant.save()
        bind_tenant(tenant)
        settings.INFO_EMAIL = "info@platform.com"

        # Ensure CONTACT_EMAIL extra_setting is absent / empty
        Setting.objects.filter(name="CONTACT_EMAIL").delete()
        assert tenant_contact_email() == "info@platform.com"

    def test_no_tenant_uses_info_email_settings(
        self, monkeypatch, settings, db
    ):
        from extra_settings.models import Setting

        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.INFO_EMAIL = "info@global.com"
        Setting.objects.filter(name="CONTACT_EMAIL").delete()
        assert tenant_contact_email() == "info@global.com"

    def test_webside_safety(self, bind_tenant, tenant_factory, settings, db):
        """webside.gr: Tenant.contact_email is empty → falls back cleanly."""
        from extra_settings.models import Setting

        tenant = tenant_factory("ws-contact-email")
        bind_tenant(tenant)
        settings.INFO_EMAIL = "info@webside.gr"
        Setting.objects.filter(name="CONTACT_EMAIL").delete()
        assert tenant_contact_email() == "info@webside.gr"


class TestTenantSiteName:
    """``tenant_site_name()`` — store_name → name → settings.SITE_NAME."""

    def test_store_name_wins_when_set(
        self, bind_tenant, tenant_factory, settings
    ):
        tenant = tenant_factory("site-name-1")
        tenant.name = "Internal Tenant Name"
        tenant.store_name = "Branded Store"
        tenant.save()
        bind_tenant(tenant)
        settings.SITE_NAME = "SETTINGS_IGNORED"
        assert tenant_site_name() == "Branded Store"

    def test_falls_back_to_name_when_store_name_empty(
        self, bind_tenant, tenant_factory, settings
    ):
        tenant = tenant_factory("site-name-2")
        tenant.name = "Internal Tenant Name"
        tenant.store_name = ""
        tenant.save()
        bind_tenant(tenant)
        settings.SITE_NAME = "SETTINGS_IGNORED"
        assert tenant_site_name() == "Internal Tenant Name"

    def test_falls_back_to_settings_when_both_empty(
        self, bind_tenant, tenant_factory, settings
    ):
        tenant = tenant_factory("site-name-3")
        tenant.name = ""
        tenant.store_name = ""
        tenant.save()
        bind_tenant(tenant)
        settings.SITE_NAME = "Platform Default"
        assert tenant_site_name() == "Platform Default"

    def test_no_tenant_uses_settings(self, monkeypatch, settings):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.SITE_NAME = "GlobalShop"
        assert tenant_site_name() == "GlobalShop"

    def test_webside_safety_name_and_store_name_agree(
        self, bind_tenant, tenant_factory, settings
    ):
        """webside.gr: seed migration sets both fields to "Webside", so
        the store_name-first reorder is a no-op for the platform tenant.
        """
        tenant = tenant_factory("ws-site-name")
        tenant.name = "Webside"
        tenant.store_name = "Webside"
        tenant.save()
        bind_tenant(tenant)
        settings.SITE_NAME = "Webside"
        assert tenant_site_name() == "Webside"


# ---------------------------------------------------------------------------
# Phase 2B: MFA TOTP issuer
# ---------------------------------------------------------------------------


class TestTenantTotpIssuer:
    def test_returns_tenant_issuer(self, bind_tenant, tenant_factory):
        tenant = tenant_factory("totp-issuer-1")
        tenant.totp_issuer = "MyShop"
        tenant.save()
        bind_tenant(tenant)
        assert tenant_totp_issuer() == "MyShop"

    def test_falls_back_to_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        tenant = tenant_factory("totp-issuer-2")
        tenant.totp_issuer = ""
        tenant.save()
        bind_tenant(tenant)
        settings.MFA_TOTP_ISSUER = "Platform TOTP"
        assert tenant_totp_issuer() == "Platform TOTP"

    def test_no_tenant_uses_settings(self, monkeypatch, settings):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.MFA_TOTP_ISSUER = "GlobalShop"
        assert tenant_totp_issuer() == "GlobalShop"

    def test_webside_safety(self, bind_tenant, tenant_factory, settings):
        """webside.gr: Tenant.totp_issuer empty → falls back to settings."""
        tenant = tenant_factory("ws-totp")
        bind_tenant(tenant)
        settings.MFA_TOTP_ISSUER = "Webside"
        assert tenant_totp_issuer() == "Webside"


# ---------------------------------------------------------------------------
# Phase 2B: Meta CAPI helpers
# ---------------------------------------------------------------------------


class TestTenantMetaCapiAccessToken:
    """No-fallback contract: tenant-only, settings are NEVER consulted."""

    def test_returns_tenant_value(self, bind_tenant, tenant_factory):
        tenant = tenant_factory("capi-token-1")
        tenant.meta_capi_access_token = "EAAshoptoken"
        tenant.save()
        bind_tenant(tenant)
        assert tenant_meta_capi_access_token() == "EAAshoptoken"

    def test_empty_tenant_field_ignores_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        tenant = tenant_factory("capi-token-2")
        tenant.meta_capi_access_token = ""
        tenant.save()
        bind_tenant(tenant)
        settings.META_CAPI_ACCESS_TOKEN = "SHOULD_NOT_BE_USED"
        assert tenant_meta_capi_access_token() == ""

    def test_no_active_tenant_ignores_settings(self, monkeypatch, settings):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.META_CAPI_ACCESS_TOKEN = "SHOULD_NOT_BE_USED"
        assert tenant_meta_capi_access_token() == ""


class TestTenantMetaCapiDatasetId:
    """No-fallback contract: tenant-only, settings are NEVER consulted."""

    def test_returns_tenant_value(self, bind_tenant, tenant_factory):
        tenant = tenant_factory("capi-dsid-1")
        tenant.meta_capi_dataset_id = "9999888877776666"
        tenant.save()
        bind_tenant(tenant)
        assert tenant_meta_capi_dataset_id() == "9999888877776666"

    def test_empty_tenant_field_ignores_meta_pixel_id_setting(
        self, bind_tenant, tenant_factory, settings
    ):
        tenant = tenant_factory("capi-dsid-2")
        tenant.meta_capi_dataset_id = ""
        tenant.save()
        bind_tenant(tenant)
        settings.META_PIXEL_ID = "SHOULD_NOT_BE_USED"
        assert tenant_meta_capi_dataset_id() == ""


class TestTenantMetaPixelId:
    """No-fallback contract: tenant-only, settings are NEVER consulted."""

    def test_returns_tenant_value(self, bind_tenant, tenant_factory):
        tenant = tenant_factory("pixel-id-1")
        tenant.meta_pixel_id = "5555666677778888"
        tenant.save()
        bind_tenant(tenant)
        assert tenant_meta_pixel_id() == "5555666677778888"

    def test_empty_tenant_field_ignores_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        tenant = tenant_factory("pixel-id-2")
        tenant.meta_pixel_id = ""
        tenant.save()
        bind_tenant(tenant)
        settings.META_PIXEL_ID = "SHOULD_NOT_BE_USED"
        assert tenant_meta_pixel_id() == ""


# ---------------------------------------------------------------------------
# Phase 2B: MFA adapter — get_totp_issuer
# ---------------------------------------------------------------------------


class TestMFAAdapterTotpIssuer:
    """MFAAdapter.get_totp_issuer() must read per-tenant issuer."""

    def test_returns_tenant_issuer_when_set(self, bind_tenant, tenant_factory):
        from core.adapter import MFAAdapter

        tenant = tenant_factory("mfa-issuer-1")
        tenant.totp_issuer = "BrandShop"
        tenant.save()
        bind_tenant(tenant)

        adapter = MFAAdapter()
        assert adapter.get_totp_issuer() == "BrandShop"

    def test_falls_back_to_allauth_app_settings_when_tenant_field_empty(
        self, bind_tenant, tenant_factory, monkeypatch, settings
    ):
        import allauth.mfa.app_settings as allauth_mfa_settings

        from core.adapter import MFAAdapter

        tenant = tenant_factory("mfa-issuer-2")
        tenant.totp_issuer = ""
        tenant.save()
        bind_tenant(tenant)

        # The env-backed platform fallback must be EMPTY for the
        # allauth app_settings fallback below to be reachable.
        settings.MFA_TOTP_ISSUER = ""
        monkeypatch.setattr(
            allauth_mfa_settings, "TOTP_ISSUER", "PlatformIssuer"
        )
        adapter = MFAAdapter()
        assert adapter.get_totp_issuer() == "PlatformIssuer"

    def test_no_tenant_uses_allauth_app_settings(self, monkeypatch, settings):
        import allauth.mfa.app_settings as allauth_mfa_settings

        from core.adapter import MFAAdapter

        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.MFA_TOTP_ISSUER = ""
        monkeypatch.setattr(allauth_mfa_settings, "TOTP_ISSUER", "FallbackSite")
        adapter = MFAAdapter()
        assert adapter.get_totp_issuer() == "FallbackSite"


# ---------------------------------------------------------------------------
# Phase 2B: MetaCapiClient — per-tenant pixel_id + access_token
# ---------------------------------------------------------------------------


class TestMetaCapiClientTenantCredentials:
    def test_client_uses_tenant_credentials(
        self, bind_tenant, tenant_factory, settings
    ):
        from meta_capi.client import MetaCapiClient

        tenant = tenant_factory("capi-client-1")
        tenant.meta_pixel_id = "111122223333"
        tenant.meta_capi_access_token = "EAAtoken"
        tenant.save()
        bind_tenant(tenant)
        settings.META_PIXEL_ID = "SHOULD_NOT_BE_USED"
        settings.META_CAPI_ACCESS_TOKEN = "SHOULD_NOT_BE_USED"

        client = MetaCapiClient()
        assert client.pixel_id == "111122223333"
        assert client.access_token == "EAAtoken"

    def test_client_stays_empty_when_tenant_empty_ignoring_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        from meta_capi.client import MetaCapiClient

        tenant = tenant_factory("capi-client-2")
        tenant.meta_pixel_id = ""
        tenant.meta_capi_access_token = ""
        tenant.save()
        bind_tenant(tenant)
        settings.META_PIXEL_ID = "SHOULD_NOT_BE_USED"
        settings.META_CAPI_ACCESS_TOKEN = "SHOULD_NOT_BE_USED"

        client = MetaCapiClient()
        assert client.pixel_id == ""
        assert client.access_token == ""

    def test_explicit_constructor_args_win_over_tenant(
        self, bind_tenant, tenant_factory
    ):
        """Explicit kwargs must still override tenant credentials."""
        from meta_capi.client import MetaCapiClient

        tenant = tenant_factory("capi-client-3")
        tenant.meta_pixel_id = "tenant_pixel"
        tenant.meta_capi_access_token = "tenant_token"
        tenant.save()
        bind_tenant(tenant)

        client = MetaCapiClient(
            pixel_id="explicit_pixel",
            access_token="explicit_token",
        )
        assert client.pixel_id == "explicit_pixel"
        assert client.access_token == "explicit_token"

    def test_webside_safety_empty_fields_stay_empty(
        self, bind_tenant, tenant_factory, settings
    ):
        from meta_capi.client import MetaCapiClient

        tenant = tenant_factory("ws-capi-client")
        # All credential fields default to ""
        bind_tenant(tenant)
        settings.META_PIXEL_ID = "SHOULD_NOT_BE_USED"
        settings.META_CAPI_ACCESS_TOKEN = "SHOULD_NOT_BE_USED"

        client = MetaCapiClient()
        assert client.pixel_id == ""
        assert client.access_token == ""


# ---------------------------------------------------------------------------
# Phase 2B: is_capi_enabled — reads tenant credentials
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIsCapiEnabledTenantAware:
    def test_disabled_when_kill_switch_off(
        self, bind_tenant, tenant_factory, settings
    ):
        from extra_settings.models import Setting

        from meta_capi.services import is_capi_enabled

        tenant = tenant_factory("capi-en-1")
        tenant.meta_pixel_id = "123456"
        tenant.meta_capi_access_token = "EAAtoken"
        tenant.save()
        bind_tenant(tenant)
        Setting.objects.update_or_create(
            name="META_CAPI_ENABLED",
            defaults={"value": False, "value_type": "bool"},
        )
        assert not is_capi_enabled()

    def test_enabled_when_kill_switch_on_and_credentials_present(
        self, bind_tenant, tenant_factory, settings
    ):
        from extra_settings.models import Setting

        from meta_capi.services import is_capi_enabled

        tenant = tenant_factory("capi-en-2")
        tenant.meta_pixel_id = "123456"
        tenant.meta_capi_access_token = "EAAtoken"
        tenant.save()
        bind_tenant(tenant)
        Setting.objects.update_or_create(
            name="META_CAPI_ENABLED",
            defaults={"value": True, "value_type": "bool"},
        )
        assert is_capi_enabled()

    def test_disabled_when_credentials_empty_even_if_toggle_on(
        self, bind_tenant, tenant_factory, settings
    ):
        """Empty tenant credentials + settings populated + kill switch ON
        must STILL be disabled — settings are never a fallback source."""
        from extra_settings.models import Setting

        from meta_capi.services import is_capi_enabled

        tenant = tenant_factory("capi-en-3")
        tenant.meta_pixel_id = ""
        tenant.meta_capi_access_token = ""
        tenant.save()
        bind_tenant(tenant)
        settings.META_PIXEL_ID = "SHOULD_NOT_BE_USED"
        settings.META_CAPI_ACCESS_TOKEN = "SHOULD_NOT_BE_USED"
        Setting.objects.update_or_create(
            name="META_CAPI_ENABLED",
            defaults={"value": True, "value_type": "bool"},
        )
        assert not is_capi_enabled()


# ---------------------------------------------------------------------------
# stripe_credentials()
# ---------------------------------------------------------------------------


class TestStripeCredentials:
    """No-fallback contract: tenant-only, settings are NEVER consulted.

    The platform-account concept is gone entirely (there is no
    ``stripe_use_platform_account`` field, and no ``STRIPE_LIVE_
    SECRET_KEY``/``STRIPE_TEST_SECRET_KEY``/``STRIPE_LIVE_MODE``/
    ``STRIPE_PUBLISHABLE_KEY`` settings) — mirrors ``viva_wallet_
    credentials()``/``acs_credentials()``/``box_now_credentials()``.
    """

    def test_tenant_key_wins(self, bind_tenant, tenant_factory):
        from tenant.credentials import stripe_credentials

        tenant = tenant_factory("stripe-own-key")
        tenant.stripe_secret_key = "sk_live_tenant"
        tenant.stripe_publishable_key = "pk_live_tenant"
        tenant.save(
            update_fields=["stripe_secret_key", "stripe_publishable_key"]
        )
        bind_tenant(tenant)

        creds = stripe_credentials()
        assert creds["secret_key"] == "sk_live_tenant"
        assert creds["publishable_key"] == "pk_live_tenant"
        assert creds["live_mode"] is True

    def test_keyless_tenant_gets_no_fallback(
        self, bind_tenant, tenant_factory, settings
    ):
        # There is no fallback source left at all — money must never
        # silently route through anything the operator didn't paste
        # into THIS tenant's row.
        from tenant.credentials import stripe_credentials

        tenant = tenant_factory("stripe-keyless")
        bind_tenant(tenant)

        creds = stripe_credentials()
        assert creds["secret_key"] == ""
        assert creds["publishable_key"] == ""
        assert creds["live_mode"] is False

    def test_returns_empty_when_no_active_tenant(self, monkeypatch):
        # Public schema — management commands, platform routines. No
        # tenant row means no Stripe identity, full stop.
        from tenant.credentials import stripe_credentials

        monkeypatch.setattr(connection, "tenant", None, raising=False)

        creds = stripe_credentials()
        assert creds["secret_key"] == ""
        assert creds["publishable_key"] == ""
        assert creds["live_mode"] is False

    def test_test_mode_key_resolves_live_mode_false(
        self, bind_tenant, tenant_factory
    ):
        from tenant.credentials import stripe_credentials

        tenant = tenant_factory("stripe-test-mode")
        tenant.stripe_secret_key = "sk_test_tenant"
        tenant.save(update_fields=["stripe_secret_key"])
        bind_tenant(tenant)

        creds = stripe_credentials()
        assert creds["secret_key"] == "sk_test_tenant"
        assert creds["live_mode"] is False


class TestVivaWalletSourceAndMode:
    """``VivaWalletPaymentProvider`` — ``source_code`` "Default" fallback
    lives in ``order/payment.py`` (not ``credentials.py``), so an empty
    tenant source_code still resolves through the provider, not here."""

    def test_source_code_and_live_mode_from_tenant(
        self, bind_tenant, tenant_factory, settings
    ):
        settings.VIVA_WALLET_SOURCE_CODE = "SHOULD_NOT_BE_USED"
        settings.VIVA_WALLET_LIVE_MODE = False
        tenant = tenant_factory("viva-source")
        tenant.viva_wallet_source_code = "T1234"
        tenant.viva_wallet_live_mode = True
        tenant.save(
            update_fields=[
                "viva_wallet_source_code",
                "viva_wallet_live_mode",
            ]
        )
        bind_tenant(tenant)

        creds = viva_wallet_credentials()
        assert creds["source_code"] == "T1234"
        assert creds["live_mode"] is True

    def test_unset_source_code_and_live_mode_ignore_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        settings.VIVA_WALLET_SOURCE_CODE = "SHOULD_NOT_BE_USED"
        settings.VIVA_WALLET_LIVE_MODE = True
        tenant = tenant_factory("viva-mode-no-fallback")
        bind_tenant(tenant)

        creds = viva_wallet_credentials()
        assert creds["source_code"] == ""
        assert creds["live_mode"] is False
