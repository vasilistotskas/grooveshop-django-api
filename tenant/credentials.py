"""Per-tenant credential helpers.

Most helpers read the named field from ``connection.tenant`` and fall
back to the matching global setting when the tenant field is empty (or
when there is no active tenant — public schema, management commands,
Celery workers without a TenantTask, tests).

Third-party CREDENTIAL helpers are the deliberate exception: Viva
Wallet, ACS, BoxNow, and Meta CAPI/Pixel values live ONLY on the
``Tenant`` model — there is no platform-wide settings fallback for
them. A merchant's payment/shipping/ad-tracking secrets must never
silently leak across tenants (or resurrect a stale platform-wide env
var) just because the operator hasn't pasted their own keys in yet.
Empty means "not configured for this tenant", full stop — callers
(``PayWayService.is_provider_configured``, the ACS/BoxNow shipping
carrier adapters, ``meta_capi.services.is_capi_enabled``) treat that
as "unavailable", not as "ask settings".

Design rules:
- Never import at module level from ``tenant.models`` — import inside
  the function so this module is safe to import before Django apps are
  fully loaded.
- All helpers are pure value getters with no side effects.
- Empty string is treated as "not configured" for every field.
"""

from __future__ import annotations

from typing import TypedDict

from django.conf import settings
from django.db import connection


class VivaWalletCredentials(TypedDict):
    merchant_id: str
    api_key: str
    client_id: str
    client_secret: str
    webhook_verification_key: str
    source_code: str
    live_mode: bool


class StripeCredentials(TypedDict):
    secret_key: str
    publishable_key: str
    live_mode: bool


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


def tenant_admin_recipients() -> list[str]:
    """Return merchant-operations alert recipients for the active tenant.

    Operational alerts (failed shipment creation, stale-shipment
    digests, unmatched COD payouts) concern the TENANT's store
    operators — the people who can fix an address or reconcile a
    payout — not the platform owner. Priority:
      1. ``Tenant.contact_email`` + ``Tenant.owner_email`` (deduped)
      2. ``settings.ADMINS`` addresses — platform fallback, which is
         also the public-schema / no-tenant behaviour.
    """
    recipients: list[str] = []
    for field in ("contact_email", "owner_email"):
        value = _get_tenant_field(field)
        if value and value not in recipients:
            recipients.append(value)
    if recipients:
        return recipients
    return [email for _name, email in getattr(settings, "ADMINS", [])]


def tenant_site_name() -> str:
    """Return the tenant's display name for email/branding contexts.

    Priority:
      1. ``Tenant.store_name`` — the customer-facing branding field
         (blank by default; what ``MyAdminSite`` already uses for
         ``site_header``/``site_title``).
      2. ``Tenant.name`` — the always-set internal tenant name.
      3. ``settings.SITE_NAME`` — platform fallback (public schema,
         no active tenant, or a tenant row with both fields empty).

    The webside.gr seed migration (``0002_seed_webside_tenant``) sets
    both ``name`` and ``store_name`` to ``"Webside"``, so this
    reordering is a no-op for the platform tenant.
    """
    return _get_tenant_field("store_name") or _get_tenant_field(
        "name", "SITE_NAME"
    )


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

    Tenant-only — no platform fallback. Empty means Meta CAPI is not
    configured for this tenant; ``meta_capi.services.is_capi_enabled``
    treats that as "disabled", not an error.
    """
    return _get_tenant_field("meta_capi_access_token")


def tenant_meta_capi_dataset_id() -> str:
    """Return the Meta CAPI dataset ID for the active tenant.

    The dataset ID is the pixel ID used for server-side dedup.
    Tenant-only — no platform fallback.
    """
    return _get_tenant_field("meta_capi_dataset_id")


def tenant_meta_pixel_id() -> str:
    """Return the browser-side Meta Pixel ID for the active tenant.

    Tenant-only — no platform fallback. The storefront gets pixel IDs
    exclusively from the tenant config (``TenantConfigSerializer``).
    """
    return _get_tenant_field("meta_pixel_id")


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


def viva_wallet_credentials() -> VivaWalletCredentials:
    """Return all Viva Wallet credentials for the current tenant.

    Tenant-only — NO platform-wide fallback (removed: money must never
    silently route through env-var credentials the operator did not
    explicitly configure for THIS tenant; mirrors the ``stripe_
    credentials()`` policy). Empty values mean Viva Wallet is NOT
    configured for this tenant:

    * ``PayWayService.is_provider_configured("viva_wallet")`` hides the
      pay-way from the shopper-facing list and rejects checkout-session
      creation.
    * ``OrderViewSet._validate_pay_way_for_order`` rejects order
      creation against an unconfigured Viva pay-way.
    * ``VivaWalletPaymentProvider.__init__`` raises
      ``ImproperlyConfigured`` if instantiated anyway.

    ``live_mode`` is a plain nullable ``BooleanField`` on ``Tenant``:
    unset (``None``) means "not configured", which resolves to
    ``False`` here — never "ask settings".

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
            "source_code":               str,
            "live_mode":                 bool,
        }
    """
    tenant = getattr(connection, "tenant", None)
    return {
        "merchant_id": _get_tenant_field("viva_wallet_merchant_id"),
        "api_key": _get_tenant_field("viva_wallet_api_key"),
        "client_id": _get_tenant_field("viva_wallet_client_id"),
        "client_secret": _get_tenant_field("viva_wallet_client_secret"),
        "webhook_verification_key": _get_tenant_field(
            "viva_wallet_webhook_verification_key"
        ),
        "source_code": _get_tenant_field("viva_wallet_source_code"),
        "live_mode": bool(getattr(tenant, "viva_wallet_live_mode", None)),
    }


# ---------------------------------------------------------------------------
# Payments — Stripe
# ---------------------------------------------------------------------------


def stripe_credentials() -> StripeCredentials:
    """Return the Stripe identity for the current tenant.

    Resolution — deliberately NOT the unconditional-fallback pattern the
    other helpers use, because money must never silently route to the
    platform's Stripe account:

      1. ``Tenant.stripe_secret_key`` when set — the tenant's own
         account.
      2. The platform ``STRIPE_LIVE/TEST_SECRET_KEY`` settings ONLY when
         ``Tenant.stripe_use_platform_account`` is True (the founding
         tenant during migration), or when there is no active tenant at
         all (public schema — management commands, platform routines).
      3. Otherwise ``""`` — Stripe is unavailable for this tenant and
         callers must treat the provider as unconfigured.

    Returns:
        {
            "secret_key":      str,   # "" == Stripe unavailable
            "publishable_key": str,
            "live_mode":       bool,
        }
    """
    tenant = getattr(connection, "tenant", None)
    in_tenant = (
        tenant is not None
        and getattr(tenant, "schema_name", "public") != "public"
    )

    secret_key = ""
    if in_tenant:
        secret_key = getattr(tenant, "stripe_secret_key", "") or ""

    if not secret_key and (
        not in_tenant or getattr(tenant, "stripe_use_platform_account", False)
    ):
        live = bool(getattr(settings, "STRIPE_LIVE_MODE", False))
        secret_key = (
            getattr(settings, "STRIPE_LIVE_SECRET_KEY", "")
            if live
            else getattr(settings, "STRIPE_TEST_SECRET_KEY", "")
        ) or ""

    publishable_key = _get_tenant_field(
        "stripe_publishable_key", "STRIPE_PUBLISHABLE_KEY"
    )
    live_mode = secret_key.startswith(("sk_live_", "rk_live_"))
    return {
        "secret_key": secret_key,
        "publishable_key": publishable_key,
        "live_mode": live_mode,
    }


# ---------------------------------------------------------------------------
# Shipping — ACS
# ---------------------------------------------------------------------------


def acs_credentials() -> dict[str, str]:
    """Return all ACS courier credentials for the current tenant.

    Tenant-only — NO platform-wide fallback. Empty means ACS is not
    configured for this tenant: ``AcsClient.__init__`` raises
    ``AcsConfigError`` if instantiated anyway, ``AcsCarrier.
    is_kind_enabled`` disables both ACS kinds so checkout never offers
    it, and the ACS fanout Celery tasks (poll tracking, pickup list,
    station sync, COD reconcile) skip the tenant cleanly.

    Platform-scoped transport config (``ACS_API_BASE_URL``,
    ``ACS_HTTP_TIMEOUT``, ``ACS_SUPPORTED_COUNTRIES``,
    ``ACS_STALE_SHIPMENT_DAYS``) is NOT per-merchant identity and stays
    in ``settings`` — read directly by ``AcsClient`` / the tasks, not
    through this helper.

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
        "api_key": _get_tenant_field("acs_api_key"),
        "company_id": _get_tenant_field("acs_company_id"),
        "company_password": _get_tenant_field("acs_company_password"),
        "user_id": _get_tenant_field("acs_user_id"),
        "user_password": _get_tenant_field("acs_user_password"),
        "billing_code": _get_tenant_field("acs_billing_code"),
        # ``shipping_acs/config.py:station_origin()`` derives this value
        # from the billing code when the explicit field is empty — both
        # this helper and that derivation path remain valid.
        "station_origin": _get_tenant_field("acs_station_origin"),
    }


# ---------------------------------------------------------------------------
# Shipping — BoxNow
# ---------------------------------------------------------------------------


def box_now_credentials() -> dict[str, str]:
    """Return all BoxNow credentials for the current tenant.

    Tenant-only — NO platform-wide fallback. Empty means BoxNow is not
    configured for this tenant: ``BoxNowClient.__init__`` raises
    ``BoxNowConfigError`` if instantiated anyway, ``BoxNowCarrier.
    is_kind_enabled`` disables pickup-point so checkout never offers
    it, and the webhook view returns 503 when ``webhook_secret`` is
    empty (it cannot verify a signature with no secret).

    ``partner_id`` is also surfaced via ``TenantConfigSerializer`` (public)
    for the storefront BoxNow widget, but it's included here so the
    ``BoxNowClient`` constructor receives a single source of truth.

    Platform-scoped transport config (``BOXNOW_API_BASE_URL``,
    ``BOXNOW_LOCATION_API_BASE_URL``, ``BOXNOW_HTTP_TIMEOUT``) is NOT
    per-merchant identity and stays in ``settings``.

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
        "client_id": _get_tenant_field("box_now_client_id"),
        "client_secret": _get_tenant_field("box_now_client_secret"),
        "partner_id": _get_tenant_field("box_now_partner_id"),
        "warehouse_id": _get_tenant_field("box_now_warehouse_id"),
        "notify_phone": _get_tenant_field("box_now_notify_phone"),
        "webhook_secret": _get_tenant_field("box_now_webhook_secret"),
    }
