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
     when the matching settings value IS populated.
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
    tenant_logo_url,
    tenant_meta_capi_access_token,
    tenant_meta_capi_dataset_id,
    tenant_meta_pixel_id,
    tenant_reply_to,
    tenant_site_name,
    tenant_totp_issuer,
    viva_wallet_credentials,
)
from tests.utils.staff import store_tenant

# ---------------------------------------------------------------------------
# _get_tenant_field
# ---------------------------------------------------------------------------


class TestGetTenantField:
    """Tests for the underlying single-field resolver."""

    def test_falls_back_to_settings_when_tenant_field_empty(
        self, bind_tenant, db, settings
    ):
        tenant = store_tenant("cred_tenant_2")
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
        self, bind_tenant, db, settings
    ):
        tenant = store_tenant("cred_tenant_prio")
        tenant.viva_wallet_api_key = "TENANT_WINS"
        tenant.save()
        bind_tenant(tenant)
        settings.VIVA_WALLET_API_KEY = "SETTINGS_IGNORED"

        result = _get_tenant_field("viva_wallet_api_key", "VIVA_WALLET_API_KEY")
        assert result == "TENANT_WINS"


# ---------------------------------------------------------------------------
# Third-party credentials — tenant-only, NO platform fallback
# ---------------------------------------------------------------------------


def _group(helper, field_prefix, setting_prefix, values):
    return [
        pytest.param(
            helper,
            key,
            f"{field_prefix}{key}",
            f"{setting_prefix}{key.upper()}",
            value,
            id=f"{helper.__name__}-{key}",
        )
        for key, value in values.items()
    ]


def _single(helper, field, setting, value):
    return pytest.param(helper, None, field, setting, value, id=helper.__name__)


_TENANT_ONLY = [
    *_group(
        viva_wallet_credentials,
        "viva_wallet_",
        "VIVA_WALLET_",
        {
            "merchant_id": "T_MID",
            "api_key": "T_APIKEY",
            "client_id": "T_CID",
            "client_secret": "T_CSECRET",
            "webhook_verification_key": "T_VK",
            "source_code": "T1234",
            # Nullable flag: unset resolves to False, never to settings.
            "live_mode": True,
        },
    ),
    *_group(
        acs_credentials,
        "acs_",
        "ACS_",
        {
            "api_key": "T_AKEY",
            "company_id": "T_ACID",
            "company_password": "T_ACPW",
            "user_id": "T_AUID",
            "user_password": "T_AUPW",
            # Greek characters must pass through unchanged.
            "billing_code": "2ΑΚ89587",
            "station_origin": "ΓΣ",
        },
    ),
    *_group(
        box_now_credentials,
        "box_now_",
        "BOXNOW_",
        {
            "client_id": "T_BCL",
            "client_secret": "T_BCS",
            "partner_id": "12345",
            "warehouse_id": "7",
            "notify_phone": "+30690000001",
            "webhook_secret": "T_WHS",
        },
    ),
    _single(
        tenant_meta_capi_access_token,
        "meta_capi_access_token",
        "META_CAPI_ACCESS_TOKEN",
        "EAAshoptoken",
    ),
    _single(
        tenant_meta_capi_dataset_id,
        "meta_capi_dataset_id",
        "META_PIXEL_ID",
        "9999888877776666",
    ),
    _single(
        tenant_meta_pixel_id,
        "meta_pixel_id",
        "META_PIXEL_ID",
        "5555666677778888",
    ),
]


def _resolve(helper, key):
    result = helper()
    return result if key is None else result[key]


def _unset(value):
    return False if isinstance(value, bool) else ""


def _populated(value):
    return True if isinstance(value, bool) else "SHOULD_NOT_BE_USED"


@pytest.mark.parametrize(
    ("helper", "key", "field", "setting", "value"), _TENANT_ONLY
)
class TestTenantOnlyCredentials:
    """Tenant field when set; otherwise empty — even with the matching
    settings value populated (no leakage from the platform env)."""

    @pytest.mark.django_db
    def test_returns_the_tenant_value(
        self, bind_tenant, helper, key, field, setting, value
    ):
        bind_tenant(store_tenant("cred_tenant", **{field: value}))
        assert _resolve(helper, key) == value

    @pytest.mark.django_db
    def test_unset_tenant_field_ignores_settings(
        self, bind_tenant, settings, helper, key, field, setting, value
    ):
        setattr(settings, setting, _populated(value))
        bind_tenant(store_tenant("cred_tenant"))
        assert _resolve(helper, key) == _unset(value)

    def test_no_active_tenant_ignores_settings(
        self, bind_tenant, settings, helper, key, field, setting, value
    ):
        setattr(settings, setting, _populated(value))
        bind_tenant(None)
        assert _resolve(helper, key) == _unset(value)


# ---------------------------------------------------------------------------
# Phase 2B: Email helpers
# ---------------------------------------------------------------------------


class TestTenantFromEmail:
    def test_an_unverified_merchant_address_is_never_used_as_from(
        self, bind_tenant, db, settings
    ):
        """DMARC safety: the relay can only sign for domains it has a
        DKIM key for. An address the platform has not verified would
        fail the merchant domain's alignment, so the tenant brand rides
        the DISPLAY NAME on the platform-authenticated address instead.
        Setting from_email alone must change nothing."""
        tenant = store_tenant("email_from_1")
        tenant.from_email = "shop@brand.com"
        tenant.store_name = "Brand Shop"
        tenant.save()
        bind_tenant(tenant)
        settings.DEFAULT_FROM_EMAIL = "noreply@platform.com"
        assert tenant_from_email() == "Brand Shop <noreply@platform.com>"
        assert "shop@brand.com" not in tenant_from_email()

    def test_a_verified_merchant_address_becomes_the_from(
        self, bind_tenant, db, settings
    ):
        """The white-label case. Once the platform has authenticated the
        store's domain on the relay, the relay holds a DKIM key for it,
        so the merchant's own address is both usable and preferable —
        customers see the shop's domain, not the platform's."""
        tenant = store_tenant("email_from_verified")
        tenant.from_email = "orders@brand.com"
        tenant.from_email_verified = True
        tenant.store_name = "Brand Shop"
        tenant.save()
        bind_tenant(tenant)
        settings.DEFAULT_FROM_EMAIL = "noreply@platform.com"
        assert tenant_from_email() == "Brand Shop <orders@brand.com>"

    def test_the_flag_alone_does_nothing_without_an_address(
        self, bind_tenant, db, settings
    ):
        """Verified but blank must not produce "Store <>", which is not
        a valid RFC 5322 address."""
        tenant = store_tenant("email_from_flag_only")
        tenant.from_email = ""
        tenant.from_email_verified = True
        tenant.store_name = "Flag Only"
        tenant.save()
        bind_tenant(tenant)
        settings.DEFAULT_FROM_EMAIL = "noreply@platform.com"
        assert tenant_from_email() == "Flag Only <noreply@platform.com>"

    def test_falls_back_to_settings(self, bind_tenant, db, settings):
        tenant = store_tenant("email_from_2")
        tenant.from_email = ""
        tenant.store_name = "Fallback Store"
        tenant.save()
        bind_tenant(tenant)
        settings.DEFAULT_FROM_EMAIL = "noreply@platform.com"
        assert tenant_from_email() == "Fallback Store <noreply@platform.com>"

    def test_no_tenant_uses_settings(self, monkeypatch, settings):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.DEFAULT_FROM_EMAIL = "platform@example.com"
        assert tenant_from_email() == "platform@example.com"

    def test_webside_safety_empty_tenant_field(self, bind_tenant, db, settings):
        """webside.gr: empty from_email → platform address with the
        store display name (envelope/domain unchanged)."""
        tenant = store_tenant("ws_from_email")
        tenant.store_name = "Webside"
        tenant.save()
        bind_tenant(tenant)
        settings.DEFAULT_FROM_EMAIL = "noreply@webside.gr"
        assert tenant_from_email() == "Webside <noreply@webside.gr>"


class TestTenantContactEmail:
    def test_returns_tenant_contact_email_first(self, bind_tenant, db):
        tenant = store_tenant("contact_email_1")
        tenant.contact_email = "contact@shop.com"
        tenant.save()
        bind_tenant(tenant)
        assert tenant_contact_email() == "contact@shop.com"

    def test_falls_back_to_extra_setting(self, bind_tenant, db):
        """When tenant.contact_email is empty reads CONTACT_EMAIL extra_setting."""
        from extra_settings.models import Setting

        tenant = store_tenant("contact_email_2")
        tenant.contact_email = ""
        tenant.save()
        bind_tenant(tenant)

        Setting.objects.update_or_create(
            name="CONTACT_EMAIL",
            defaults={"value": "extra@example.com", "value_type": "string"},
        )
        assert tenant_contact_email() == "extra@example.com"

    def test_store_without_contact_address_has_none(
        self, bind_tenant, db, settings
    ):
        # A store never borrows the platform's address: until
        # 2026-09-10 INFO_EMAIL was the first store's, so every other
        # store's Reply-To and contact inbox silently pointed there.
        from extra_settings.models import Setting

        tenant = store_tenant("contact_email_3")
        tenant.contact_email = ""
        tenant.save()
        bind_tenant(tenant)
        settings.INFO_EMAIL = "info@platform.com"

        Setting.objects.filter(name="CONTACT_EMAIL").delete()
        assert tenant_contact_email() == ""

    def test_no_tenant_uses_info_email_settings(
        self, monkeypatch, settings, db
    ):
        from extra_settings.models import Setting

        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.INFO_EMAIL = "info@global.com"
        Setting.objects.filter(name="CONTACT_EMAIL").delete()
        assert tenant_contact_email() == "info@global.com"

    def test_public_schema_uses_the_platform_address(
        self, monkeypatch, settings, db
    ):
        # Platform-schema mail (billing notices) carries the PLATFORM's
        # own contact address.
        from types import SimpleNamespace

        from django_tenants.utils import get_public_schema_name
        from extra_settings.models import Setting

        monkeypatch.setattr(
            connection,
            "tenant",
            SimpleNamespace(schema_name=get_public_schema_name()),
            raising=False,
        )
        settings.INFO_EMAIL = "info@platform.com"
        Setting.objects.filter(name="CONTACT_EMAIL").delete()
        assert tenant_contact_email() == "info@platform.com"


class TestTenantReplyTo:
    """``tenant_reply_to()`` — the ``Reply-To`` list for outbound mail."""

    def test_store_with_contact_address(self, bind_tenant, db):
        tenant = store_tenant("reply_to_1")
        tenant.contact_email = "contact@shop.com"
        tenant.save()
        bind_tenant(tenant)
        assert tenant_reply_to() == ["contact@shop.com"]

    def test_store_without_contact_address_emits_no_header(
        self, bind_tenant, db, settings
    ):
        # ``EmailMessage`` joins the list verbatim, so ``[""]`` would put
        # a literal empty ``Reply-To:`` header on the wire; ``[]`` emits
        # none at all.
        from django.core.mail import EmailMultiAlternatives
        from extra_settings.models import Setting

        tenant = store_tenant("reply_to_2")
        tenant.contact_email = ""
        tenant.save()
        bind_tenant(tenant)
        settings.INFO_EMAIL = "info@platform.com"
        Setting.objects.filter(name="CONTACT_EMAIL").delete()

        assert tenant_reply_to() == []
        message = EmailMultiAlternatives(
            "s",
            "b",
            "from@example.com",
            ["to@example.com"],
            reply_to=tenant_reply_to(),
        ).message()
        assert "Reply-To" not in message


class TestTenantSiteName:
    """``tenant_site_name()`` — store_name → name → settings.SITE_NAME."""

    def test_store_name_wins_when_set(self, bind_tenant, db, settings):
        tenant = store_tenant("site_name_1")
        tenant.name = "Internal Tenant Name"
        tenant.store_name = "Branded Store"
        tenant.save()
        bind_tenant(tenant)
        settings.SITE_NAME = "SETTINGS_IGNORED"
        assert tenant_site_name() == "Branded Store"

    def test_falls_back_to_name_when_store_name_empty(
        self, bind_tenant, db, settings
    ):
        tenant = store_tenant("site_name_2")
        tenant.name = "Internal Tenant Name"
        tenant.store_name = ""
        tenant.save()
        bind_tenant(tenant)
        settings.SITE_NAME = "SETTINGS_IGNORED"
        assert tenant_site_name() == "Internal Tenant Name"

    def test_falls_back_to_settings_when_both_empty(
        self, bind_tenant, db, settings
    ):
        tenant = store_tenant("site_name_3")
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
        self, bind_tenant, db, settings
    ):
        """webside.gr: seed migration sets both fields to "Webside", so
        the store_name-first reorder is a no-op for the platform tenant.
        """
        tenant = store_tenant("ws_site_name")
        tenant.name = "Webside"
        tenant.store_name = "Webside"
        tenant.save()
        bind_tenant(tenant)
        settings.SITE_NAME = "Webside"
        assert tenant_site_name() == "Webside"


class TestTenantLogoUrl:
    """``tenant_logo_url()`` — Tenant.logo_light_url, no platform
    fallback (there is no platform logo asset; empty means "render
    the template's static fallback logo")."""

    def test_returns_tenant_logo_when_set(self, bind_tenant, db):
        tenant = store_tenant("logo_url_1")
        tenant.logo_light_url = "https://cdn.example.com/logo.svg"
        tenant.save()
        bind_tenant(tenant)
        assert tenant_logo_url() == "https://cdn.example.com/logo.svg"

    def test_empty_when_tenant_has_no_logo(self, bind_tenant, db):
        tenant = store_tenant("logo_url_2")
        tenant.logo_light_url = ""
        tenant.save()
        bind_tenant(tenant)
        assert tenant_logo_url() == ""

    def test_empty_with_no_active_tenant(self, monkeypatch):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        assert tenant_logo_url() == ""


# ---------------------------------------------------------------------------
# Phase 2B: MFA TOTP issuer
# ---------------------------------------------------------------------------


class TestTenantTotpIssuer:
    def test_returns_tenant_issuer(self, bind_tenant, db):
        tenant = store_tenant("totp_issuer_1")
        tenant.totp_issuer = "MyShop"
        tenant.save()
        bind_tenant(tenant)
        assert tenant_totp_issuer() == "MyShop"

    def test_empty_issuer_uses_the_store_name(self, bind_tenant, db):
        # Never a platform-wide string: an authenticator entry is
        # labelled with the store the user enrolled on.
        tenant = store_tenant("totp_issuer_2")
        tenant.totp_issuer = ""
        tenant.store_name = "Brand Shop"
        tenant.save()
        bind_tenant(tenant)
        assert tenant_totp_issuer() == "Brand Shop"

    def test_no_tenant_uses_the_platform_site_name(self, monkeypatch, settings):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.SITE_NAME = "GlobalShop"
        assert tenant_totp_issuer() == "GlobalShop"


# ---------------------------------------------------------------------------
# Phase 2B: MFA adapter — get_totp_issuer
# ---------------------------------------------------------------------------


class TestMFAAdapterTotpIssuer:
    """MFAAdapter.get_totp_issuer() must read per-tenant issuer."""

    def test_returns_tenant_issuer_when_set(self, bind_tenant, db):
        from core.adapter import MFAAdapter

        tenant = store_tenant("mfa_issuer_1")
        tenant.totp_issuer = "BrandShop"
        tenant.save()
        bind_tenant(tenant)

        adapter = MFAAdapter()
        assert adapter.get_totp_issuer() == "BrandShop"

    def test_empty_tenant_field_uses_the_store_name_never_allauth_settings(
        self, bind_tenant, db, monkeypatch
    ):
        # allauth's own chain (MFA_TOTP_ISSUER, then the Site
        # framework's current site) must never be consulted: on a
        # multi-tenant deployment both resolve to ONE store's name.
        import allauth.mfa.app_settings as allauth_mfa_settings

        from core.adapter import MFAAdapter

        tenant = store_tenant("mfa_issuer_2")
        tenant.totp_issuer = ""
        tenant.store_name = "Brand Shop"
        tenant.save()
        bind_tenant(tenant)
        monkeypatch.setattr(
            allauth_mfa_settings, "TOTP_ISSUER", "PlatformIssuer"
        )
        adapter = MFAAdapter()
        assert adapter.get_totp_issuer() == "Brand Shop"

    def test_rp_entity_name_is_the_store_name(self, bind_tenant, db):
        from core.adapter import MFAAdapter

        tenant = store_tenant("mfa_rp_name")
        tenant.store_name = "Brand Shop"
        tenant.save()
        bind_tenant(tenant)
        entity = MFAAdapter().get_public_key_credential_rp_entity()
        assert entity["name"] == "Brand Shop"


# ---------------------------------------------------------------------------
# Phase 2B: MetaCapiClient — per-tenant pixel_id + access_token
# ---------------------------------------------------------------------------


class TestMetaCapiClientTenantCredentials:
    def test_client_uses_tenant_credentials(self, bind_tenant, db, settings):
        from meta_capi.client import MetaCapiClient

        tenant = store_tenant("capi_client_1")
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
        self, bind_tenant, db, settings
    ):
        from meta_capi.client import MetaCapiClient

        tenant = store_tenant("capi_client_2")
        tenant.meta_pixel_id = ""
        tenant.meta_capi_access_token = ""
        tenant.save()
        bind_tenant(tenant)
        settings.META_PIXEL_ID = "SHOULD_NOT_BE_USED"
        settings.META_CAPI_ACCESS_TOKEN = "SHOULD_NOT_BE_USED"

        client = MetaCapiClient()
        assert client.pixel_id == ""
        assert client.access_token == ""

    def test_explicit_constructor_args_win_over_tenant(self, bind_tenant, db):
        """Explicit kwargs must still override tenant credentials."""
        from meta_capi.client import MetaCapiClient

        tenant = store_tenant("capi_client_3")
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
        self, bind_tenant, db, settings
    ):
        from meta_capi.client import MetaCapiClient

        tenant = store_tenant("ws_capi_client")
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
    def test_disabled_when_kill_switch_off(self, bind_tenant, db, settings):
        from extra_settings.models import Setting

        from meta_capi.services import is_capi_enabled

        tenant = store_tenant("capi_en_1")
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
        self, bind_tenant, db, settings
    ):
        from extra_settings.models import Setting

        from meta_capi.services import is_capi_enabled

        tenant = store_tenant("capi_en_2")
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
        self, bind_tenant, db, settings
    ):
        """Empty tenant credentials + settings populated + kill switch ON
        must STILL be disabled — settings are never a fallback source."""
        from extra_settings.models import Setting

        from meta_capi.services import is_capi_enabled

        tenant = store_tenant("capi_en_3")
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

    def test_tenant_key_wins(self, bind_tenant, db):
        from tenant.credentials import stripe_credentials

        tenant = store_tenant("stripe_own_key")
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

    def test_keyless_tenant_gets_no_fallback(self, bind_tenant, db, settings):
        # There is no fallback source left at all — money must never
        # silently route through anything the operator didn't paste
        # into THIS tenant's row.
        from tenant.credentials import stripe_credentials

        tenant = store_tenant("stripe_keyless")
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

    def test_test_mode_key_resolves_live_mode_false(self, bind_tenant, db):
        from tenant.credentials import stripe_credentials

        tenant = store_tenant("stripe_test_mode")
        tenant.stripe_secret_key = "sk_test_tenant"
        tenant.save(update_fields=["stripe_secret_key"])
        bind_tenant(tenant)

        creds = stripe_credentials()
        assert creds["secret_key"] == "sk_test_tenant"
        assert creds["live_mode"] is False
