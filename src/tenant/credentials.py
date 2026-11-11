"""Per-tenant credential helpers.

Most helpers read the named field from the bound tenant row
(``tenant.membership.bound_tenant`` -- never a bare ``FakeTenant``) and fall
back to the matching global setting when the tenant field is empty (or
when there is no active tenant — public schema, management commands,
Celery workers without a TenantTask, tests).

Third-party CREDENTIAL helpers are the deliberate exception: Stripe,
Viva Wallet, ACS, BoxNow, myDATA, and Meta CAPI/Pixel values live ONLY on the
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

import logging
from typing import TypedDict

from django.conf import settings
from django.db import connection

from tenant.membership import bound_tenant

logger = logging.getLogger(__name__)


class VivaWalletCredentials(TypedDict):
    merchant_id: str
    api_key: str
    client_id: str
    client_secret: str
    webhook_verification_key: str
    source_code: str
    live_mode: bool


class BoxNowCredentials(TypedDict):
    client_id: str
    client_secret: str
    partner_id: str
    warehouse_id: str
    notify_phone: str
    webhook_secret: str
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
    tenant = bound_tenant()
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


def in_tenant_schema() -> bool:
    """True when a store's schema is active (not the public schema, not
    a bare management/Celery context)."""
    from django_tenants.utils import get_public_schema_name

    schema = getattr(getattr(connection, "tenant", None), "schema_name", "")
    return bool(schema) and schema != get_public_schema_name()


def tenant_contact_email() -> str:
    """Return the public contact address for the active tenant.

    Priority:
      1. ``Tenant.contact_email``
      2. ``extra_settings`` key ``CONTACT_EMAIL``
      3. In a store's schema: ``""`` — a store with no contact address
         has none. Its outbound mail then carries no ``Reply-To`` and
         contact-form submissions are skipped with a warning
         (``contact/tasks.py``); it never borrows the platform's (or,
         as ``settings.INFO_EMAIL`` was until 2026-09-10, the first
         store's) address.
      4. Public schema / no active tenant: ``settings.INFO_EMAIL`` — the
         PLATFORM's own contact address, for platform-schema mail such
         as billing notices to store owners.
    """
    tenant_email = _get_tenant_field("contact_email")
    if tenant_email:
        return tenant_email
    from core.store_settings import store_setting

    try:
        setting_value = store_setting("CONTACT_EMAIL") or ""
    except Exception:
        # A database blip must not break every outbound email, but it
        # must not be swallowed silently either.
        logger.warning(
            "Could not read the CONTACT_EMAIL setting",
            exc_info=True,
        )
    else:
        if setting_value:
            return setting_value
    if in_tenant_schema():
        return ""
    return getattr(settings, "INFO_EMAIL", "") or ""


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


def _branded_primary() -> tuple[str, str] | None:
    """The 600 and 700 of the store's primary colour scale, when it is
    the store's own.

    Read from the theme document of the store's CURRENT release, the one
    source the storefront draws its look from (``/colors/light/primary``),
    through the cached chrome read the storefront's own requests share
    (``builder.published.published_index``): no query once the release is
    warm, and no cache of its own. The default locale is asked because a
    colour has no translation.

    ``None`` — the platform palette — when the store is not in a store
    schema (platform mail renders under ``public``, and a Celery task with
    no tenant has none), has no release or no theme document, or its scale
    is the platform's own default: every new store starts from that
    scale, so "the store has a primary scale" does NOT mean "the merchant
    chose it". Treating it so would repaint every unbranded store's mail
    grey. The default is the catalogue the storefront pinned with the
    seeds, which every design shares for the primary scale.

    A document that cannot be read never fails a send: the store gets the
    platform palette and a warning is logged.
    """
    if not in_tenant_schema():
        return None
    from builder import published, seeding
    from builder.contrast import HEX

    try:
        theme = published.published_index(
            settings.PARLER_DEFAULT_LANGUAGE_CODE
        )["theme"]
        if theme is None:
            return None
        scale = theme["settings"]["colors"]["light"]["primary"]["scale"]
        # A catalogue with no theme defaults has nothing to compare with.
        defaults = seeding.seed_catalogue().roots["theme"].defaults or {}
        default = defaults["colors"]["light"]["primary"]["scale"]
        primary, dark = scale["600"], scale["700"]
    except Exception:
        # A theme that cannot be read must not stop outbound mail, but it
        # must not be swallowed silently either.
        logger.warning(
            "Could not read the published theme for email colours",
            exc_info=True,
        )
        return None
    # The colours go straight into the email's <style> block.
    if not all(isinstance(c, str) and HEX.match(c) for c in (primary, dark)):
        logger.warning(
            "The published theme's primary scale is not hex: email colours"
            " fall back to the platform palette"
        )
        return None
    if (primary.lower(), dark.lower()) == (
        default["600"].lower(),
        default["700"].lower(),
    ):
        return None
    return primary, dark


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

    A store's brand is its primary colour scale in its published theme
    document (``_branded_primary``): shade 600 is the button, link and
    header colour, shade 700 the button's hover. A store that has not
    changed that scale, or has no theme document yet, gets the platform
    palette above, which is what its mail wore before it was themable.

    Only the primary scale is read. The accent and the status colours
    (``/colors/light/accent``, ``…/success``) are stored as the colour a
    store paints, and the colour an UNCHANGED store paints depends on its
    storefront design, which the API does not know: comparing them with a
    default would repaint the stores of the other design. The secondary
    button therefore stays the platform's green.

    The scale holds hex, never a Tailwind colour NAME ("blue", "zinc"),
    so it can go into email CSS without a name→hex table.
    """
    theme = dict(_DEFAULT_EMAIL_THEME)
    if (brand := _branded_primary()) is not None:
        primary, dark = brand
        theme["primary"] = primary
        theme["header"] = primary
        theme["primary_dark"] = dark
    return theme


# ---------------------------------------------------------------------------
# Authentication — MFA
# ---------------------------------------------------------------------------


def tenant_totp_issuer() -> str:
    """Return the TOTP issuer shown in authenticator apps.

    ``Tenant.totp_issuer`` when set, otherwise the store's own display
    name (``tenant_site_name``) — an authenticator entry is labelled
    with the store the user enrolled on, never with a platform-wide
    string. There is no settings knob: allauth's own fallback would be
    the Site framework's current site, i.e. another store's name.
    """
    return _get_tenant_field("totp_issuer") or tenant_site_name()


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
    tenant = bound_tenant()
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
    ``ACS_HTTP_TIMEOUT``, ``ACS_STALE_SHIPMENT_DAYS``) is NOT
    per-merchant identity and stays in ``settings`` — read directly by
    ``AcsClient`` / the tasks, not through this helper. Which countries
    ACS ships to is a per-tenant ``shipping.ShippingRate`` question.

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
# Invoicing — myDATA
# ---------------------------------------------------------------------------


def mydata_credentials() -> dict[str, str]:
    """Return the myDATA (AADE) credentials for the current tenant.

    Tenant-only — NO platform-wide fallback: each store invoices under its
    own TAXISnet account. Empty means myDATA is not configured for this
    tenant (``order.mydata.config.MyDataConfig.is_ready`` is False). The
    on/off switch, environment and series settings are not credentials
    and stay ``extra_settings`` rows.

    Returns:
        {
            "user_id":          str,   # aade-user-id header
            "subscription_key": str,   # Ocp-Apim-Subscription-Key header
        }
    """
    return {
        "user_id": _get_tenant_field("mydata_user_id"),
        "subscription_key": _get_tenant_field("mydata_subscription_key"),
    }


# ---------------------------------------------------------------------------
# Shipping — BoxNow
# ---------------------------------------------------------------------------


def box_now_credentials() -> BoxNowCredentials:
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

    ``live_mode`` says which BoxNow environment the keys belong to
    (production when True, stage otherwise); ``BoxNowClient`` maps it to
    the platform's ``BOXNOW_PRODUCTION_*`` / ``BOXNOW_STAGE_*`` hosts.
    Platform-scoped transport config (those hosts, ``BOXNOW_HTTP_TIMEOUT``)
    is NOT per-merchant identity and stays in ``settings``.

    Returns:
        {
            "client_id":      str,
            "client_secret":  str,
            "partner_id":     str,
            "warehouse_id":   str,
            "notify_phone":   str,
            "webhook_secret": str,
            "live_mode":      bool,
        }
    """
    tenant = bound_tenant()
    return {
        "client_id": _get_tenant_field("box_now_client_id"),
        "client_secret": _get_tenant_field("box_now_client_secret"),
        "partner_id": _get_tenant_field("box_now_partner_id"),
        "warehouse_id": _get_tenant_field("box_now_warehouse_id"),
        "notify_phone": _get_tenant_field("box_now_notify_phone"),
        "webhook_secret": _get_tenant_field("box_now_webhook_secret"),
        "live_mode": bool(getattr(tenant, "box_now_live_mode", None)),
    }


def tenant_resend_webhook_secret() -> str:
    """The bound store's own Resend webhook signing secret, ``""`` when it
    has none. Tenant-only, with no platform fallback: a delivery to a store's
    host is verified with that store's secret or not at all."""
    return _get_tenant_field("resend_webhook_secret")


def tenant_resend_api_key() -> str:
    """The bound store's own Resend sending key, ``""`` when it has none.

    Tenant-only, like the payment and carrier credentials: a store's key
    never falls back to the platform's (``settings.RESEND_API_KEY``), and
    the choice between the two is ``emails.resend.api_key``'s, made on
    whether the store sends from its own verified domain.
    """
    return _get_tenant_field("resend_api_key")
