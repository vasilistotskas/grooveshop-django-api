"""Per-tenant credential helpers.

Each helper reads the named field from ``connection.tenant`` and falls
back to the matching global setting when the tenant field is empty (or
when there is no active tenant — public schema, management commands,
Celery workers without a TenantTask, tests).

Design rules:
- Never import at module level from ``tenant.models`` — import inside
  the function so this module is safe to import before Django apps are
  fully loaded.
- All helpers are pure value getters with no side effects.
- Empty string is treated as "not configured" for every field.
"""

from __future__ import annotations

from django.conf import settings
from django.db import connection


def _get_tenant_field(
    field_name: str, settings_fallback: str | None = None
) -> str:
    """Return the named field from the active tenant, or ``""`` if not set.

    ``settings_fallback`` is the name of the ``settings`` attribute to
    use when the tenant field is empty or there is no active tenant.
    When ``settings_fallback`` is ``None`` the function returns ``""``
    in the fallback case.
    """
    tenant = getattr(connection, "tenant", None)
    if tenant is not None:
        value = getattr(tenant, field_name, "") or ""
        if value:
            return value
    if settings_fallback is not None:
        return getattr(settings, settings_fallback, "") or ""
    return ""


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


def tenant_from_email() -> str:
    """Return the outbound sender address for the active tenant.

    Priority: ``Tenant.from_email`` → ``settings.DEFAULT_FROM_EMAIL``.
    """
    return _get_tenant_field("from_email", "DEFAULT_FROM_EMAIL")


def tenant_contact_email() -> str:
    """Return the public contact address for the active tenant.

    Priority:
      1. ``Tenant.contact_email``
      2. ``extra_settings`` key ``CONTACT_EMAIL``
      3. ``settings.INFO_EMAIL``
    """
    tenant_email = _get_tenant_field("contact_email")
    if tenant_email:
        return tenant_email
    try:
        from extra_settings.models import Setting  # noqa: PLC0415

        setting_value = Setting.get("CONTACT_EMAIL", default="") or ""
        if setting_value:
            return setting_value
    except Exception:  # pragma: no cover — extra_settings not installed
        pass
    return getattr(settings, "INFO_EMAIL", "") or ""


# ---------------------------------------------------------------------------
# Authentication — MFA
# ---------------------------------------------------------------------------


def tenant_totp_issuer() -> str:
    """Return the TOTP issuer name for the active tenant.

    Priority: ``Tenant.totp_issuer`` → ``settings.MFA_TOTP_ISSUER``.
    """
    return _get_tenant_field("totp_issuer", "MFA_TOTP_ISSUER")


# ---------------------------------------------------------------------------
# Analytics — Meta Conversions API
# ---------------------------------------------------------------------------


def tenant_meta_capi_access_token() -> str:
    """Return the Meta CAPI access token for the active tenant.

    Priority:
      ``Tenant.meta_capi_access_token`` → ``settings.META_CAPI_ACCESS_TOKEN``.
    """
    return _get_tenant_field("meta_capi_access_token", "META_CAPI_ACCESS_TOKEN")


def tenant_meta_capi_dataset_id() -> str:
    """Return the Meta CAPI dataset ID for the active tenant.

    The dataset ID is the pixel ID used for server-side dedup.

    Priority: ``Tenant.meta_capi_dataset_id`` → ``settings.META_PIXEL_ID``.
    """
    return _get_tenant_field("meta_capi_dataset_id", "META_PIXEL_ID")


def tenant_meta_pixel_id() -> str:
    """Return the browser-side Meta Pixel ID for the active tenant.

    Priority: ``Tenant.meta_pixel_id`` → ``settings.META_PIXEL_ID``.
    """
    return _get_tenant_field("meta_pixel_id", "META_PIXEL_ID")


# ---------------------------------------------------------------------------
# Bot Protection — Turnstile
# ---------------------------------------------------------------------------


def tenant_turnstile_secret_key() -> str:
    """Return the Cloudflare Turnstile secret key for the active tenant.

    Priority:
      ``Tenant.turnstile_secret_key`` → ``settings.TURNSTILE_SECRET_KEY``.
    """
    return _get_tenant_field("turnstile_secret_key", "TURNSTILE_SECRET_KEY")


# ---------------------------------------------------------------------------
# Payments — Viva Wallet
# ---------------------------------------------------------------------------


def viva_wallet_credentials() -> dict[str, str]:
    """Return all Viva Wallet credentials for the current tenant.

    Resolution order per key:
      1. ``Tenant.<field>`` (set by operator in Django admin)
      2. ``settings.VIVA_WALLET_*`` (platform-wide env-var fallback)

    The token cache key in ``VivaWalletPaymentProvider`` is already
    tenant-scoped via ``tenant.cache.make_tenant_key``, so no extra
    scoping is needed here.

    Returns:
        {
            "merchant_id":               str,
            "api_key":                   str,
            "client_id":                 str,
            "client_secret":             str,
            "webhook_verification_key":  str,
        }
    """
    return {
        "merchant_id": _get_tenant_field(
            "viva_wallet_merchant_id", "VIVA_WALLET_MERCHANT_ID"
        ),
        "api_key": _get_tenant_field(
            "viva_wallet_api_key", "VIVA_WALLET_API_KEY"
        ),
        "client_id": _get_tenant_field(
            "viva_wallet_client_id", "VIVA_WALLET_CLIENT_ID"
        ),
        "client_secret": _get_tenant_field(
            "viva_wallet_client_secret", "VIVA_WALLET_CLIENT_SECRET"
        ),
        "webhook_verification_key": _get_tenant_field(
            "viva_wallet_webhook_verification_key",
            "VIVA_WALLET_WEBHOOK_VERIFICATION_KEY",
        ),
    }


# ---------------------------------------------------------------------------
# Shipping — ACS
# ---------------------------------------------------------------------------


def acs_credentials() -> dict[str, str]:
    """Return all ACS courier credentials for the current tenant.

    The raw strings are returned unchanged — Greek billing codes (e.g.
    ``ΑΚ12345678``) are preserved as-is. The locale-decimal conversion
    required by ACS numeric fields (``Cod_Ammount``, ``Weight``) is the
    caller's responsibility (see ``shipping_acs.services._kg_from_grams``).

    Returns:
        {
            "api_key":          str,
            "company_id":       str,
            "company_password": str,
            "user_id":          str,
            "user_password":    str,
            "billing_code":     str,
            "station_origin":   str,
        }
    """
    return {
        "api_key": _get_tenant_field("acs_api_key", "ACS_API_KEY"),
        "company_id": _get_tenant_field("acs_company_id", "ACS_COMPANY_ID"),
        "company_password": _get_tenant_field(
            "acs_company_password", "ACS_COMPANY_PASSWORD"
        ),
        "user_id": _get_tenant_field("acs_user_id", "ACS_USER_ID"),
        "user_password": _get_tenant_field(
            "acs_user_password", "ACS_USER_PASSWORD"
        ),
        "billing_code": _get_tenant_field(
            "acs_billing_code", "ACS_BILLING_CODE"
        ),
        # ``ACS_STATION_ORIGIN`` has no model field before Phase 1 migration;
        # ``shipping_acs/config.py:station_origin()`` derives this value from
        # the billing code when the explicit field is empty — both this helper
        # and that derivation path remain valid.
        "station_origin": _get_tenant_field(
            "acs_station_origin", "ACS_STATION_ORIGIN"
        ),
    }


# ---------------------------------------------------------------------------
# Shipping — BoxNow
# ---------------------------------------------------------------------------


def box_now_credentials() -> dict[str, str]:
    """Return all BoxNow credentials for the current tenant.

    ``partner_id`` is also surfaced via ``TenantConfigSerializer`` (public)
    for the storefront BoxNow widget, but it's included here so the
    ``BoxNowClient`` constructor receives a single source of truth.

    ``webhook_secret`` has no Tenant model field yet (the secret must
    never reach the browser); it falls back exclusively to
    ``settings.BOXNOW_WEBHOOK_SECRET``.  When per-tenant webhook secrets
    are required, add ``box_now_webhook_secret`` to the Tenant model and
    update the ``"box_now_webhook_secret"`` field name below.

    Returns:
        {
            "client_id":      str,
            "client_secret":  str,
            "partner_id":     str,
            "warehouse_id":   str,
            "notify_phone":   str,
            "webhook_secret": str,
        }
    """
    return {
        "client_id": _get_tenant_field("box_now_client_id", "BOXNOW_CLIENT_ID"),
        "client_secret": _get_tenant_field(
            "box_now_client_secret", "BOXNOW_CLIENT_SECRET"
        ),
        "partner_id": _get_tenant_field(
            "box_now_partner_id", "BOXNOW_PARTNER_ID"
        ),
        "warehouse_id": _get_tenant_field(
            "box_now_warehouse_id", "BOXNOW_WAREHOUSE_ID"
        ),
        "notify_phone": _get_tenant_field(
            "box_now_notify_phone", "BOXNOW_NOTIFY_PHONE"
        ),
        # No Tenant model field for webhook secret — settings-only for now.
        "webhook_secret": _get_tenant_field(
            "box_now_webhook_secret", "BOXNOW_WEBHOOK_SECRET"
        ),
    }
