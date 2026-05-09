"""Unit tests for tenant/credentials.py helpers.

Tests verify:
- Tenant field value is returned when set (non-empty).
- Falls back to the settings value when the tenant field is empty.
- Falls back to the settings value when connection.tenant is None.
- Returns "" when both are absent.
- Greek strings (ACS billing codes) pass through unchanged.
- Phase 2B: email, MFA TOTP issuer, Meta CAPI, and Turnstile helpers.
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
    tenant_totp_issuer,
    tenant_turnstile_secret_key,
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
    def test_returns_all_keys(self, monkeypatch, settings):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.VIVA_WALLET_MERCHANT_ID = "m1"
        settings.VIVA_WALLET_API_KEY = "k1"
        settings.VIVA_WALLET_CLIENT_ID = "c1"
        settings.VIVA_WALLET_CLIENT_SECRET = "s1"
        settings.VIVA_WALLET_WEBHOOK_VERIFICATION_KEY = "v1"

        creds = viva_wallet_credentials()
        assert creds["merchant_id"] == "m1"
        assert creds["api_key"] == "k1"
        assert creds["client_id"] == "c1"
        assert creds["client_secret"] == "s1"
        assert creds["webhook_verification_key"] == "v1"

    def test_tenant_overrides_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        tenant = tenant_factory("viva-tenant")
        tenant.viva_wallet_merchant_id = "TENANT_MID"
        tenant.viva_wallet_api_key = "TENANT_APIKEY"
        tenant.viva_wallet_client_id = "TENANT_CID"
        tenant.viva_wallet_client_secret = "TENANT_CSECRET"
        tenant.viva_wallet_webhook_verification_key = "TENANT_VK"
        tenant.save()
        bind_tenant(tenant)
        settings.VIVA_WALLET_MERCHANT_ID = "SETTINGS_MID"
        settings.VIVA_WALLET_API_KEY = "SETTINGS_APIKEY"

        creds = viva_wallet_credentials()
        assert creds["merchant_id"] == "TENANT_MID"
        assert creds["api_key"] == "TENANT_APIKEY"
        assert creds["client_id"] == "TENANT_CID"
        assert creds["client_secret"] == "TENANT_CSECRET"
        assert creds["webhook_verification_key"] == "TENANT_VK"

    def test_partial_tenant_override_falls_back_per_field(
        self, bind_tenant, tenant_factory, settings
    ):
        """Tenant sets merchant_id but not api_key — api_key falls back."""
        tenant = tenant_factory("viva-partial")
        tenant.viva_wallet_merchant_id = "TENANT_MID"
        tenant.viva_wallet_api_key = ""
        tenant.save()
        bind_tenant(tenant)
        settings.VIVA_WALLET_API_KEY = "SETTINGS_APIKEY"

        creds = viva_wallet_credentials()
        assert creds["merchant_id"] == "TENANT_MID"
        assert creds["api_key"] == "SETTINGS_APIKEY"

    def test_webside_safety_empty_tenant_uses_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        """webside.gr: all Tenant credential fields are empty — must fall back."""
        tenant = tenant_factory("ws-viva-creds")
        # All credential fields default to "" — don't override
        bind_tenant(tenant)
        settings.VIVA_WALLET_MERCHANT_ID = "WEBSIDE_MID"
        settings.VIVA_WALLET_API_KEY = "WEBSIDE_APIKEY"
        settings.VIVA_WALLET_CLIENT_ID = "WEBSIDE_CID"
        settings.VIVA_WALLET_CLIENT_SECRET = "WEBSIDE_CSECRET"
        settings.VIVA_WALLET_WEBHOOK_VERIFICATION_KEY = "WEBSIDE_VK"

        creds = viva_wallet_credentials()
        assert creds["merchant_id"] == "WEBSIDE_MID"
        assert creds["api_key"] == "WEBSIDE_APIKEY"
        assert creds["client_id"] == "WEBSIDE_CID"
        assert creds["client_secret"] == "WEBSIDE_CSECRET"
        assert creds["webhook_verification_key"] == "WEBSIDE_VK"


# ---------------------------------------------------------------------------
# acs_credentials()
# ---------------------------------------------------------------------------


class TestAcsCredentials:
    def test_returns_all_keys(self, monkeypatch, settings):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.ACS_API_KEY = "AKEY"
        settings.ACS_COMPANY_ID = "ACID"
        settings.ACS_COMPANY_PASSWORD = "ACPW"
        settings.ACS_USER_ID = "AUID"
        settings.ACS_USER_PASSWORD = "AUPW"
        settings.ACS_BILLING_CODE = "2ΑΚ89587"
        settings.ACS_STATION_ORIGIN = "ΑΚ"

        creds = acs_credentials()
        assert creds["api_key"] == "AKEY"
        assert creds["company_id"] == "ACID"
        assert creds["company_password"] == "ACPW"
        assert creds["user_id"] == "AUID"
        assert creds["user_password"] == "AUPW"
        assert creds["billing_code"] == "2ΑΚ89587"
        assert creds["station_origin"] == "ΑΚ"

    def test_tenant_overrides_settings(
        self, bind_tenant, tenant_factory, settings
    ):
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
        settings.ACS_API_KEY = "S_AKEY"
        settings.ACS_BILLING_CODE = "2ΑΚ89587"

        creds = acs_credentials()
        assert creds["api_key"] == "T_AKEY"
        assert creds["billing_code"] == "9ΓΣ11111"
        assert creds["station_origin"] == "ΓΣ"

    def test_greek_billing_code_preserved(self, bind_tenant, tenant_factory):
        tenant = tenant_factory("acs-greek")
        tenant.acs_billing_code = "2ΑΚ89587"
        tenant.save()
        bind_tenant(tenant)

        creds = acs_credentials()
        assert creds["billing_code"] == "2ΑΚ89587"

    def test_webside_safety(self, bind_tenant, tenant_factory, settings):
        tenant = tenant_factory("webside-acs")
        # All fields default to ""
        bind_tenant(tenant)
        settings.ACS_API_KEY = "WS_AKEY"
        settings.ACS_COMPANY_ID = "WS_ACID"
        settings.ACS_COMPANY_PASSWORD = "WS_ACPW"
        settings.ACS_USER_ID = "WS_AUID"
        settings.ACS_USER_PASSWORD = "WS_AUPW"
        settings.ACS_BILLING_CODE = "2ΑΚ89587"

        creds = acs_credentials()
        assert creds["api_key"] == "WS_AKEY"
        assert creds["company_id"] == "WS_ACID"
        assert creds["billing_code"] == "2ΑΚ89587"


# ---------------------------------------------------------------------------
# box_now_credentials()
# ---------------------------------------------------------------------------


class TestBoxNowCredentials:
    def test_returns_all_keys(self, monkeypatch, settings):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.BOXNOW_CLIENT_ID = "BCL"
        settings.BOXNOW_CLIENT_SECRET = "BCS"
        settings.BOXNOW_PARTNER_ID = "999"
        settings.BOXNOW_WAREHOUSE_ID = "2"
        settings.BOXNOW_NOTIFY_PHONE = "+30210000001"
        settings.BOXNOW_WEBHOOK_SECRET = "WHS"

        creds = box_now_credentials()
        assert creds["client_id"] == "BCL"
        assert creds["client_secret"] == "BCS"
        assert creds["partner_id"] == "999"
        assert creds["warehouse_id"] == "2"
        assert creds["notify_phone"] == "+30210000001"
        assert creds["webhook_secret"] == "WHS"

    def test_tenant_overrides_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        tenant = tenant_factory("bn-tenant")
        tenant.box_now_client_id = "T_BCL"
        tenant.box_now_client_secret = "T_BCS"
        tenant.box_now_partner_id = "12345"
        tenant.box_now_warehouse_id = "7"
        tenant.box_now_notify_phone = "+30690000001"
        tenant.save()
        bind_tenant(tenant)
        settings.BOXNOW_CLIENT_ID = "S_BCL"
        settings.BOXNOW_PARTNER_ID = "99999"

        creds = box_now_credentials()
        assert creds["client_id"] == "T_BCL"
        assert creds["client_secret"] == "T_BCS"
        assert creds["partner_id"] == "12345"
        assert creds["warehouse_id"] == "7"
        assert creds["notify_phone"] == "+30690000001"

    def test_webhook_secret_falls_back_to_settings_only(
        self, bind_tenant, tenant_factory, settings
    ):
        """Webhook secret has no Tenant field — always reads from settings."""
        tenant = tenant_factory("bn-whs")
        bind_tenant(tenant)
        settings.BOXNOW_WEBHOOK_SECRET = "SETTINGS_WHS"

        creds = box_now_credentials()
        assert creds["webhook_secret"] == "SETTINGS_WHS"

    def test_webside_safety(self, bind_tenant, tenant_factory, settings):
        tenant = tenant_factory("webside-bn")
        # All credential fields default to ""
        bind_tenant(tenant)
        settings.BOXNOW_CLIENT_ID = "WS_BCL"
        settings.BOXNOW_CLIENT_SECRET = "WS_BCS"
        settings.BOXNOW_PARTNER_ID = "88888"
        settings.BOXNOW_WAREHOUSE_ID = "3"
        settings.BOXNOW_NOTIFY_PHONE = "+30210000000"

        creds = box_now_credentials()
        assert creds["client_id"] == "WS_BCL"
        assert creds["client_secret"] == "WS_BCS"
        assert creds["partner_id"] == "88888"
        assert creds["warehouse_id"] == "3"
        assert creds["notify_phone"] == "+30210000000"


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
    def test_returns_tenant_value(self, bind_tenant, tenant_factory):
        tenant = tenant_factory("capi-token-1")
        tenant.meta_capi_access_token = "EAAshoptoken"
        tenant.save()
        bind_tenant(tenant)
        assert tenant_meta_capi_access_token() == "EAAshoptoken"

    def test_falls_back_to_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        tenant = tenant_factory("capi-token-2")
        tenant.meta_capi_access_token = ""
        tenant.save()
        bind_tenant(tenant)
        settings.META_CAPI_ACCESS_TOKEN = "EAAplatform"
        assert tenant_meta_capi_access_token() == "EAAplatform"

    def test_webside_safety(self, bind_tenant, tenant_factory, settings):
        tenant = tenant_factory("ws-capi-token")
        bind_tenant(tenant)
        settings.META_CAPI_ACCESS_TOKEN = "EAAglobal"
        assert tenant_meta_capi_access_token() == "EAAglobal"


class TestTenantMetaCapiDatasetId:
    def test_returns_tenant_value(self, bind_tenant, tenant_factory):
        tenant = tenant_factory("capi-dsid-1")
        tenant.meta_capi_dataset_id = "9999888877776666"
        tenant.save()
        bind_tenant(tenant)
        assert tenant_meta_capi_dataset_id() == "9999888877776666"

    def test_falls_back_to_meta_pixel_id_setting(
        self, bind_tenant, tenant_factory, settings
    ):
        tenant = tenant_factory("capi-dsid-2")
        tenant.meta_capi_dataset_id = ""
        tenant.save()
        bind_tenant(tenant)
        settings.META_PIXEL_ID = "1111222233334444"
        assert tenant_meta_capi_dataset_id() == "1111222233334444"


class TestTenantMetaPixelId:
    def test_returns_tenant_value(self, bind_tenant, tenant_factory):
        tenant = tenant_factory("pixel-id-1")
        tenant.meta_pixel_id = "5555666677778888"
        tenant.save()
        bind_tenant(tenant)
        assert tenant_meta_pixel_id() == "5555666677778888"

    def test_falls_back_to_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        tenant = tenant_factory("pixel-id-2")
        tenant.meta_pixel_id = ""
        tenant.save()
        bind_tenant(tenant)
        settings.META_PIXEL_ID = "9999000011112222"
        assert tenant_meta_pixel_id() == "9999000011112222"


# ---------------------------------------------------------------------------
# Phase 2B: Turnstile secret key
# ---------------------------------------------------------------------------


class TestTenantTurnstileSecretKey:
    def test_returns_tenant_value(self, bind_tenant, tenant_factory):
        tenant = tenant_factory("ts-secret-1")
        tenant.turnstile_secret_key = "0xTenantSecret"
        tenant.save()
        bind_tenant(tenant)
        assert tenant_turnstile_secret_key() == "0xTenantSecret"

    def test_falls_back_to_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        tenant = tenant_factory("ts-secret-2")
        tenant.turnstile_secret_key = ""
        tenant.save()
        bind_tenant(tenant)
        settings.TURNSTILE_SECRET_KEY = "0xGlobalSecret"
        assert tenant_turnstile_secret_key() == "0xGlobalSecret"

    def test_no_tenant_uses_settings(self, monkeypatch, settings):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.TURNSTILE_SECRET_KEY = "0xPlatformSecret"
        assert tenant_turnstile_secret_key() == "0xPlatformSecret"

    def test_webside_safety(self, bind_tenant, tenant_factory, settings):
        """webside.gr: Tenant.turnstile_secret_key empty → falls back cleanly."""
        tenant = tenant_factory("ws-turnstile")
        bind_tenant(tenant)
        settings.TURNSTILE_SECRET_KEY = "0xWebsideSecret"
        assert tenant_turnstile_secret_key() == "0xWebsideSecret"


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
        self, bind_tenant, tenant_factory, monkeypatch
    ):
        import allauth.mfa.app_settings as allauth_mfa_settings

        from core.adapter import MFAAdapter

        tenant = tenant_factory("mfa-issuer-2")
        tenant.totp_issuer = ""
        tenant.save()
        bind_tenant(tenant)

        monkeypatch.setattr(
            allauth_mfa_settings, "TOTP_ISSUER", "PlatformIssuer"
        )
        adapter = MFAAdapter()
        assert adapter.get_totp_issuer() == "PlatformIssuer"

    def test_no_tenant_uses_allauth_app_settings(self, monkeypatch):
        import allauth.mfa.app_settings as allauth_mfa_settings

        from core.adapter import MFAAdapter

        monkeypatch.setattr(connection, "tenant", None, raising=False)
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

    def test_client_falls_back_to_settings_when_tenant_empty(
        self, bind_tenant, tenant_factory, settings
    ):
        from meta_capi.client import MetaCapiClient

        tenant = tenant_factory("capi-client-2")
        tenant.meta_pixel_id = ""
        tenant.meta_capi_access_token = ""
        tenant.save()
        bind_tenant(tenant)
        settings.META_PIXEL_ID = "global_pixel"
        settings.META_CAPI_ACCESS_TOKEN = "global_token"

        client = MetaCapiClient()
        assert client.pixel_id == "global_pixel"
        assert client.access_token == "global_token"

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

    def test_webside_safety_empty_fields_use_settings(
        self, bind_tenant, tenant_factory, settings
    ):
        from meta_capi.client import MetaCapiClient

        tenant = tenant_factory("ws-capi-client")
        # All credential fields default to ""
        bind_tenant(tenant)
        settings.META_PIXEL_ID = "ws_pixel"
        settings.META_CAPI_ACCESS_TOKEN = "ws_token"

        client = MetaCapiClient()
        assert client.pixel_id == "ws_pixel"
        assert client.access_token == "ws_token"


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
        from extra_settings.models import Setting

        from meta_capi.services import is_capi_enabled

        tenant = tenant_factory("capi-en-3")
        tenant.meta_pixel_id = ""
        tenant.meta_capi_access_token = ""
        tenant.save()
        bind_tenant(tenant)
        settings.META_PIXEL_ID = ""
        settings.META_CAPI_ACCESS_TOKEN = ""
        Setting.objects.update_or_create(
            name="META_CAPI_ENABLED",
            defaults={"value": True, "value_type": "bool"},
        )
        assert not is_capi_enabled()


# ---------------------------------------------------------------------------
# Phase 2B: get_base_email_context — INFO_EMAIL is tenant-aware
# ---------------------------------------------------------------------------


class TestGetBaseEmailContext:
    def test_info_email_uses_tenant_contact(
        self, bind_tenant, tenant_factory, settings, db
    ):
        from extra_settings.models import Setting

        from core.utils.email import get_base_email_context

        tenant = tenant_factory("ctx-email-1")
        tenant.contact_email = "contact@shopbrand.com"
        tenant.save()
        bind_tenant(tenant)
        Setting.objects.filter(name="CONTACT_EMAIL").delete()

        ctx = get_base_email_context()
        assert ctx["INFO_EMAIL"] == "contact@shopbrand.com"

    def test_info_email_falls_back_to_global_setting(
        self, bind_tenant, tenant_factory, settings, db
    ):
        from extra_settings.models import Setting

        from core.utils.email import get_base_email_context

        tenant = tenant_factory("ctx-email-2")
        tenant.contact_email = ""
        tenant.save()
        bind_tenant(tenant)
        settings.INFO_EMAIL = "platform_info@example.com"
        Setting.objects.filter(name="CONTACT_EMAIL").delete()

        ctx = get_base_email_context()
        assert ctx["INFO_EMAIL"] == "platform_info@example.com"
