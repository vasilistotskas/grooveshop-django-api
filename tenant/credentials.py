"""Per-tenant credential helpers.

Most helpers read the named field from ``connection.tenant`` and fall
back to the matching global setting when the tenant field is empty (or
when there is no active tenant — public schema, management commands,
Celery workers without a TenantTask, tests).

Third-party CREDENTIAL helpers are the deliberate exception: Stripe,
Viva Wallet, ACS, BoxNow, and Meta CAPI/Pixel values live ONLY on the
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
    """Return the outbound ``From`` header for the active tenant,
    deliverability-safe.

    All mail leaves through the PLATFORM transport, whose sending
    domain is the only one SPF/DKIM-authorized for that relay. Putting
    a merchant's own address in ``From`` (``orders@their-domain.com``)
    would fail the merchant domain's DMARC alignment — mail lands in
    spam or bounces outright. Policy:

    - Tenant context: RFC 5322 display-name form
      ``"{store name}" <DEFAULT_FROM_EMAIL>`` — authenticated platform
      envelope, tenant brand in the inbox sender line. The tenant's own
      address belongs in ``Reply-To`` (send sites already pass
      ``tenant_contact_email()``).
    - No tenant context (public/admin sends): bare
      ``DEFAULT_FROM_EMAIL``.

    ``Tenant.from_email`` is reserved for a future per-tenant transport
    feature (merchant-provided SMTP with their own domain's SPF/DKIM);
    it is deliberately NOT used as ``From`` on the platform relay.
    """
    from email.utils import formataddr

    from django.conf import settings

    default = getattr(settings, "DEFAULT_FROM_EMAIL", "") or ""
    if not default:
        # No platform sender configured — an empty address inside a
        # display-name form ("Store <>") would be invalid RFC 5322.
        return ""
    name = tenant_site_name()
    if name and getattr(connection, "tenant", None) is not None:
        return formataddr((name, default))
    return default


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
        from extra_settings.models import Setting

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


def tenant_logo_url() -> str:
    """Return the tenant's light-mode logo URL for email branding.

    Priority: ``Tenant.logo_light_url`` → ``""``. Tenant-only — no
    platform-wide fallback here. An empty string is a normal, expected
    state, not an error: ``core.utils.email_context._email_logo_url``
    treats it as "no tenant logo configured" and substitutes the
    platform's static ``logo-dark.svg`` ONLY for the platform tenant
    itself, leaving it empty for anyone else so an unbranded store's
    email never wears another store's brand (``email_base.html`` then
    renders the store name as a text wordmark).
    """
    return _get_tenant_field("logo_light_url")


# The palette ``email_base.html`` carried inline before it was made
# themable. These are the EXACT values from its old ``:root`` block —
# an unbranded tenant's mail must render byte-identically to before.
_DEFAULT_EMAIL_THEME = {
    "primary": "#2563eb",
    "primary_dark": "#1e40af",
    "secondary": "#10b981",
    "header": "#97b7ff",
}


def _customised(field: str) -> str:
    """The tenant's value ONLY when it differs from the model default.

    Every ``Tenant`` row carries the field's default — ``accent_hex``
    defaults to ``#003DFF``, ``success_hex`` to ``#16a34a`` — so "the
    field has a value" does NOT mean "the merchant chose it". Treating
    it that way would have repainted every existing store's email
    (header ``#97b7ff`` -> ``#003DFF``) purely because a default exists.

    Comparing against the field default is how the storefront token
    compiler decides too: ``server/utils/themeTokens.ts`` emits a token
    only when the tenant's value differs from ``PLATFORM_COLORS``, so a
    tenant that customised nothing emits nothing. Case-insensitive for
    the same reason it is there — the hex fields are free text.
    """
    from tenant.models import Tenant

    value = (_get_tenant_field(field) or "").strip()
    if not value:
        return ""
    default = str(Tenant._meta.get_field(field).default or "").strip()
    return "" if value.lower() == default.lower() else value


def tenant_email_theme() -> dict[str, str]:
    """Return the tenant's brand colours for email, as literal hex.

    Email clients are the reason this exists as resolved hex rather
    than CSS custom properties: caniemail records Gmail, Outlook,
    Apple Mail, Yahoo and Thunderbird as accepting the ``var()``
    FUNCTION while dropping the ``:root { --x: … }`` DECLARATION, which
    makes every ``var()`` reference invalid at computed-value time. The
    base template used 22 of them with no fallbacks, so the primary CTA
    resolved to ``background-color: transparent`` while keeping
    ``color: #ffffff !important`` — white text on a white card.

    Sources, in priority order:
      1. ``Tenant.theme_metadata["colors"]["primaryScale"]`` — the
         per-shade map the storefront token compiler already uses.
      2. ``Tenant.accent_hex`` / ``Tenant.success_hex`` — the validated
         ``#RRGGBB`` fields on the Tenant row.
      3. The platform defaults above.

    ``Tenant.primary_color`` and ``neutral_color`` are deliberately NOT
    consulted: they hold Tailwind colour NAMES ("blue", "zinc") for the
    Nuxt compiler, not hex, so they cannot go into email CSS without a
    name→hex table this does not need.
    """
    theme = dict(_DEFAULT_EMAIL_THEME)

    scale = {}
    metadata = _get_tenant_field("theme_metadata") or {}
    if isinstance(metadata, dict):
        colors = metadata.get("colors")
        if isinstance(colors, dict):
            candidate = colors.get("primaryScale")
            if isinstance(candidate, dict):
                scale = candidate

    accent = _customised("accent_hex")
    success = _customised("success_hex")

    primary = scale.get("600") or accent
    if primary:
        theme["primary"] = primary
        theme["header"] = primary
    dark = scale.get("700") or primary
    if dark:
        theme["primary_dark"] = dark
    if success:
        theme["secondary"] = success

    return theme


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

    Tenant-only — NO platform-wide fallback (mirrors ``viva_wallet_
    credentials()``/``acs_credentials()``/``box_now_credentials()``):
    money must never silently route through a platform Stripe account.
    The "current webside.gr account is just webside's" — there is no
    platform-account concept left to fall back to.

      1. ``Tenant.stripe_secret_key`` when set — the tenant's own
         account.
      2. Otherwise ``""`` — Stripe is unavailable for this tenant (or
         for the public schema, where there is no tenant row at all)
         and callers must treat the provider as unconfigured:
         ``PayWayService.is_provider_configured("stripe")`` hides the
         pay-way, ``OrderViewSet._validate_pay_way_for_order`` rejects
         order creation, and ``StripePaymentProvider.__init__`` raises
         ``ImproperlyConfigured`` if instantiated anyway.

    Returns:
        {
            "secret_key":      str,   # "" == Stripe unavailable
            "publishable_key": str,
            "live_mode":       bool,
        }
    """
    secret_key = _get_tenant_field("stripe_secret_key")
    publishable_key = _get_tenant_field("stripe_publishable_key")
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
