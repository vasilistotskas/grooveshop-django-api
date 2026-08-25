from __future__ import annotations

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_tenants.models import DomainMixin, TenantMixin, _check_schema_name
from knox.models import AbstractAuthToken
from simple_history.models import HistoricalRecords

from tenant.validators import (
    validate_reserved_schema_name,
    validate_theme_metadata,
)

from core.models import TimeStampMixinModel, UUIDModel


class TenantPlan(models.TextChoices):
    TRIAL = "trial", _("Trial")
    BASIC = "basic", _("Basic")
    PRO = "pro", _("Pro")
    ENTERPRISE = "enterprise", _("Enterprise")


class SuspendedReason(models.TextChoices):
    """Why a tenant is suspended — abuse and non-payment are different.

    ``MANUAL`` is any operator-initiated suspension (abuse, legal,
    operator judgement); ``BILLING`` is the dunning task acting on a
    lapsed term. The distinction is load-bearing: renewal-driven
    auto-reactivation (tenant/signals.py) is scoped to BILLING only, so
    recording a payment can never resurrect a store an operator took
    down on purpose.
    """

    MANUAL = "manual", _("Manual")
    BILLING = "billing", _("Billing")


class ThemePreset(models.TextChoices):
    DEFAULT = "default", _("Default")
    MINIMAL = "minimal", _("Minimal")
    BOLD = "bold", _("Bold")
    CUSTOM = "custom", _("Custom")


# Tailwind color name choices for Nuxt UI v4 compatibility
class TailwindColor(models.TextChoices):
    SLATE = "slate", "Slate"
    GRAY = "gray", "Gray"
    ZINC = "zinc", "Zinc"
    NEUTRAL = "neutral", "Neutral"
    STONE = "stone", "Stone"
    RED = "red", "Red"
    ORANGE = "orange", "Orange"
    AMBER = "amber", "Amber"
    YELLOW = "yellow", "Yellow"
    LIME = "lime", "Lime"
    GREEN = "green", "Green"
    EMERALD = "emerald", "Emerald"
    TEAL = "teal", "Teal"
    CYAN = "cyan", "Cyan"
    SKY = "sky", "Sky"
    BLUE = "blue", "Blue"
    INDIGO = "indigo", "Indigo"
    VIOLET = "violet", "Violet"
    PURPLE = "purple", "Purple"
    FUCHSIA = "fuchsia", "Fuchsia"
    PINK = "pink", "Pink"
    ROSE = "rose", "Rose"


hex_color_validator = RegexValidator(
    regex=r"^#[0-9a-fA-F]{6}$",
    message=_("Enter a valid hex color (e.g., #003DFF)."),
)


class Tenant(TenantMixin, TimeStampMixinModel, UUIDModel):
    # Redeclares ``TenantMixin.schema_name`` (abstract base — safe to
    # override) to layer ``validate_reserved_schema_name`` on top of
    # django-tenants' own PostgreSQL-identifier format check
    # (``_check_schema_name``). Field shape (max_length, unique,
    # db_index) mirrors the base field exactly; only the validators
    # list changes.
    schema_name = models.CharField(
        _("Schema name"),
        max_length=63,
        unique=True,
        db_index=True,
        validators=[_check_schema_name, validate_reserved_schema_name],
    )

    name = models.CharField(_("Name"), max_length=100)
    slug = models.SlugField(_("Slug"), max_length=100, unique=True)
    owner_email = models.EmailField(_("Owner Email"))
    is_active = models.BooleanField(_("Active"), default=True)
    suspended_at = models.DateTimeField(
        _("Suspended At"),
        null=True,
        blank=True,
        help_text=_(
            "Timestamp when this tenant was last suspended. "
            "Set automatically by the suspend admin action. "
            "Required to be at least 24 hours in the past before "
            "the tenant can be permanently destroyed."
        ),
    )

    suspended_reason = models.CharField(
        _("Suspended Reason"),
        max_length=20,
        choices=SuspendedReason.choices,
        blank=True,
        default="",
        help_text=_(
            "Why this tenant is suspended: 'manual' for operator "
            "action, 'billing' for the automated dunning task. Empty "
            "when not suspended. Renewal-driven auto-reactivation "
            "applies to 'billing' suspensions only."
        ),
    )

    # Plan / billing
    plan = models.CharField(
        _("Plan"),
        max_length=20,
        choices=TenantPlan.choices,
        default=TenantPlan.TRIAL,
    )
    paid_until = models.DateField(
        _("Paid Until"),
        null=True,
        blank=True,
        help_text=_(
            "Last day (inclusive) of the current term — trial or paid. "
            "Empty means the term never expires (legacy rows, the "
            "platform row). The daily billing task warns, dunns, and "
            "optionally suspends from this date."
        ),
    )

    # Dunning bookkeeping — written only by the billing task. The pair
    # makes the pipeline idempotent AND self-resetting: a stage is only
    # valid for the term it was recorded against, so moving paid_until
    # forward implicitly resets the dunning state with no signal.
    billing_notice_stage = models.PositiveSmallIntegerField(
        _("Billing Notice Stage"),
        default=0,
        help_text=_(
            "Highest dunning stage already notified for the current "
            "term (0 none, 1 expiry warning, 2 expired, 3 suspended)."
        ),
    )
    billing_notice_term = models.DateField(
        _("Billing Notice Term"),
        null=True,
        blank=True,
        help_text=_(
            "The paid_until value the notice stage refers to. A "
            "mismatch with the current paid_until means the stage is "
            "stale and resets to 0 on the next billing run."
        ),
    )

    # Branding
    store_name = models.CharField(
        _("Store Name"), max_length=200, blank=True, default=""
    )
    store_description = models.TextField(
        _("Store Description"), blank=True, default=""
    )
    default_locale = models.CharField(
        _("Default Locale"), max_length=10, default="el"
    )
    default_currency = models.CharField(
        _("Default Currency"), max_length=3, default="EUR"
    )

    # Assets
    logo_light_url = models.URLField(_("Logo (Light)"), blank=True, default="")
    logo_dark_url = models.URLField(_("Logo (Dark)"), blank=True, default="")
    favicon_url = models.URLField(_("Favicon URL"), blank=True, default="")

    # Theme (Nuxt UI v4 compatible)
    primary_color = models.CharField(
        _("Primary Color"),
        max_length=20,
        choices=TailwindColor.choices,
        default=TailwindColor.NEUTRAL,
    )
    neutral_color = models.CharField(
        _("Neutral Color"),
        max_length=20,
        choices=TailwindColor.choices,
        default=TailwindColor.ZINC,
    )
    accent_hex = models.CharField(
        _("Accent Hex"),
        max_length=7,
        default="#003DFF",
        validators=[hex_color_validator],
    )
    success_hex = models.CharField(
        _("Success Hex"),
        max_length=7,
        default="#16a34a",
        validators=[hex_color_validator],
    )
    warning_hex = models.CharField(
        _("Warning Hex"),
        max_length=7,
        default="#ca8a04",
        validators=[hex_color_validator],
    )
    error_hex = models.CharField(
        _("Error Hex"),
        max_length=7,
        default="#dc2626",
        validators=[hex_color_validator],
    )
    info_hex = models.CharField(
        _("Info Hex"),
        max_length=7,
        default="#2563eb",
        validators=[hex_color_validator],
    )

    # Theme preset & metadata
    theme_preset = models.CharField(
        _("Theme Preset"),
        max_length=20,
        choices=ThemePreset.choices,
        default=ThemePreset.DEFAULT,
    )
    theme_metadata = models.JSONField(
        _("Theme Metadata"),
        default=dict,
        blank=True,
        validators=[validate_theme_metadata],
        help_text=_(
            "Per-token theme overrides on top of the preset. Legal "
            "keys: radius, fontSans, container, colors.primaryScale / "
            "colors.neutralScale (shade → #RRGGBB). Anything else is "
            "rejected — the storefront token compiler ignores invalid "
            "metadata entirely."
        ),
    )

    # Feature flags
    loyalty_enabled = models.BooleanField(_("Loyalty Enabled"), default=False)
    blog_enabled = models.BooleanField(_("Blog Enabled"), default=True)
    promotions_enabled = models.BooleanField(
        _("Promotions Enabled"), default=False
    )
    gift_cards_enabled = models.BooleanField(
        _("Gift Cards Enabled"), default=False
    )
    # Gates the ENTIRE agent-gateway surface for this tenant: MCP
    # commerce tools, UCP/ACP agentic checkout, catalog feeds and the
    # storefront chatbot backend. Default True — the surface predates
    # the flag and is live for existing tenants; the flag exists so the
    # platform can opt a tenant OUT (the merchant runtime tier is the
    # AGENT_COMMERCE_ENABLED extra-setting, folded into TenantConfig).
    agent_commerce_enabled = models.BooleanField(
        _("Agent Commerce Enabled"), default=True
    )

    # Stripe Connect — dormant/reserved. The platform runs SEPARATE
    # Stripe accounts per tenant (``stripe_secret_key`` below), not
    # Connect: dj-stripe's webhook pipeline has no per-connected-account
    # event routing, while its per-schema WebhookEndpoint/APIKey design
    # maps 1:1 onto one-account-per-schema. Kept as a future option.
    stripe_connect_account_id = models.CharField(
        _("Stripe Connect Account ID"),
        max_length=255,
        blank=True,
        default="",
    )

    stripe_secret_key = models.CharField(
        _("Stripe secret key"),
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "The tenant's OWN Stripe secret key (sk_live_*, sk_test_* "
            "or a restricted rk_* key). Secret — never expose to the "
            "browser. Empty means Stripe is unavailable for this "
            "tenant — there is no platform-wide fallback."
        ),
    )

    # Public Stripe key — safe to expose to unauthenticated callers.
    # Use the per-tenant ``pk_live_*`` / ``pk_test_*`` key here.
    # Empty string means Stripe is unconfigured for this tenant — there
    # is no platform-wide fallback (mirrors stripe_secret_key).
    stripe_publishable_key = models.CharField(
        _("Stripe publishable key"),
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "Per-tenant Stripe publishable key (pk_live_* or pk_test_*). "
            "This value is public by design and is safe to expose to "
            "unauthenticated callers. Leave blank if Stripe is not "
            "configured for this tenant."
        ),
    )

    # Extra CSP origins for the storefront.
    # Each entry must be a string starting with https://, http://localhost,
    # or wss:// so that only safe origins can be added.
    allowed_csp_sources = models.JSONField(
        _("Allowed CSP sources"),
        default=list,
        blank=True,
        help_text=_(
            "Additional origins allowed by the storefront CSP "
            "(connect-src, img-src, script-src, frame-src). "
            "Each entry must start with https://, http://localhost, or wss://."
        ),
    )

    # === Analytics ===

    meta_pixel_id = models.CharField(
        _("Meta Pixel ID"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "Meta (Facebook) Pixel ID for the storefront. "
            "Digits only (e.g. '123456789012345'). "
            "Empty → browser pixel is disabled for this tenant."
        ),
    )
    tiktok_pixel_id = models.CharField(
        _("TikTok Pixel ID"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "TikTok Pixel ID for the storefront (e.g. 'C0ABCDEFGH123'). "
            "Alphanumeric only. "
            "Empty → TikTok pixel is disabled for this tenant."
        ),
    )

    ga_tracking_id = models.CharField(
        _("Google Analytics Tracking ID"),
        max_length=32,
        blank=True,
        default="",
        help_text=_(
            "Google Analytics 4 measurement ID (G-XXXXXXXXXX) or "
            "Universal Analytics property (UA-XXXXXXXX-X). "
            "Empty → GA is disabled for this tenant."
        ),
    )

    # === Authentication ===

    totp_issuer = models.CharField(
        _("TOTP Issuer"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "Issuer name shown in authenticator apps when the user "
            "scans the TOTP QR code (e.g. 'MyShop'). "
            "Empty falls back to settings.MFA_TOTP_ISSUER."
        ),
    )

    # === Social Links ===

    socials_discord = models.URLField(
        _("Discord URL"),
        blank=True,
        default="",
        help_text=_(
            "Discord server invite URL. Must use https://. "
            "Empty → link hidden in storefront footer."
        ),
    )
    socials_facebook = models.URLField(
        _("Facebook URL"),
        blank=True,
        default="",
        help_text=_(
            "Facebook page URL. Must use https://. "
            "Empty → link hidden in storefront footer."
        ),
    )
    socials_instagram = models.URLField(
        _("Instagram URL"),
        blank=True,
        default="",
        help_text=_(
            "Instagram profile URL. Must use https://. "
            "Empty → link hidden in storefront footer."
        ),
    )
    socials_pinterest = models.URLField(
        _("Pinterest URL"),
        blank=True,
        default="",
        help_text=_(
            "Pinterest profile URL. Must use https://. "
            "Empty → link hidden in storefront footer."
        ),
    )
    socials_reddit = models.URLField(
        _("Reddit URL"),
        blank=True,
        default="",
        help_text=_(
            "Reddit community/profile URL. Must use https://. "
            "Empty → link hidden in storefront footer."
        ),
    )
    socials_tiktok = models.URLField(
        _("TikTok URL"),
        blank=True,
        default="",
        help_text=_(
            "TikTok profile URL. Must use https://. "
            "Empty → link hidden in storefront footer."
        ),
    )
    socials_twitter = models.URLField(
        _("Twitter / X URL"),
        blank=True,
        default="",
        help_text=_(
            "Twitter/X profile URL. Must use https://. "
            "Empty → link hidden in storefront footer."
        ),
    )
    socials_youtube = models.URLField(
        _("YouTube URL"),
        blank=True,
        default="",
        help_text=_(
            "YouTube channel URL. Must use https://. "
            "Empty → link hidden in storefront footer."
        ),
    )

    # === Shipping — BoxNow (public field) ===

    box_now_partner_id = models.CharField(
        _("BoxNow Partner ID"),
        max_length=32,
        blank=True,
        default="",
        help_text=_(
            "BoxNow partner identifier (digits only). "
            "Used by the storefront BoxNow widget. "
            "Empty falls back to settings.BOXNOW_PARTNER_ID."
        ),
    )

    # -----------------------------------------------------------------------
    # Admin-only / server-only fields — NOT exposed via TenantConfigSerializer
    # -----------------------------------------------------------------------

    # === Email ===

    from_email = models.EmailField(
        _("From Email"),
        blank=True,
        default="",
        help_text=_(
            "Sender address for outbound email from this tenant "
            "(e.g. 'no-reply@myshop.com'). "
            "Empty falls back to settings.DEFAULT_FROM_EMAIL."
        ),
    )
    contact_email = models.EmailField(
        _("Contact Email"),
        blank=True,
        default="",
        help_text=_(
            "Public contact email shown in storefront footer and "
            "contact page. "
            "Empty falls back to settings.INFO_EMAIL."
        ),
    )

    # === Analytics (server-side) ===

    meta_capi_access_token = models.CharField(
        _("Meta CAPI Access Token"),
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "Meta Conversions API system user access token. "
            "Secret — never expose to the browser. "
            "Empty falls back to settings.META_CAPI_ACCESS_TOKEN."
        ),
    )
    meta_capi_dataset_id = models.CharField(
        _("Meta CAPI Dataset ID"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "Meta Conversions API dataset (pixel) ID used for "
            "server-side event deduplication. "
            "Empty falls back to settings.META_PIXEL_ID."
        ),
    )

    # === Payments — Viva Wallet ===

    viva_wallet_merchant_id = models.CharField(
        _("Viva Wallet Merchant ID"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "Viva Wallet merchant ID. "
            "Empty falls back to settings.VIVA_WALLET_MERCHANT_ID."
        ),
    )
    viva_wallet_api_key = models.CharField(
        _("Viva Wallet API Key"),
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "Viva Wallet API key (classic auth). "
            "Empty falls back to settings.VIVA_WALLET_API_KEY."
        ),
    )
    viva_wallet_client_id = models.CharField(
        _("Viva Wallet Client ID"),
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "Viva Wallet OAuth2 client ID. "
            "Empty falls back to settings.VIVA_WALLET_CLIENT_ID."
        ),
    )
    viva_wallet_client_secret = models.CharField(
        _("Viva Wallet Client Secret"),
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "Viva Wallet OAuth2 client secret. "
            "Empty falls back to settings.VIVA_WALLET_CLIENT_SECRET."
        ),
    )
    viva_wallet_webhook_verification_key = models.CharField(
        _("Viva Wallet Webhook Verification Key"),
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "Viva Wallet webhook verification key for HMAC validation. "
            "Empty falls back to "
            "settings.VIVA_WALLET_WEBHOOK_VERIFICATION_KEY."
        ),
    )
    viva_wallet_source_code = models.CharField(
        _("Viva Wallet Source Code"),
        max_length=32,
        blank=True,
        default="",
        help_text=_(
            "Viva Wallet Smart Checkout payment source code — a "
            "per-merchant identifier, so each tenant needs their own. "
            "Empty falls back to settings.VIVA_WALLET_SOURCE_CODE."
        ),
    )
    viva_wallet_live_mode = models.BooleanField(
        _("Viva Wallet Live Mode"),
        null=True,
        blank=True,
        default=None,
        help_text=_(
            "Whether this tenant's Viva credentials are live (demo "
            "otherwise). There is NO settings fallback: unset is read as "
            "demo, so a live merchant MUST set this explicitly or real "
            "payments are sent to the Viva demo environment."
        ),
    )

    # === Shipping — ACS ===

    acs_api_key = models.CharField(
        _("ACS API Key"),
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "ACS courier API key. Empty falls back to settings.ACS_API_KEY."
        ),
    )
    acs_company_id = models.CharField(
        _("ACS Company ID"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "ACS company identifier. "
            "Empty falls back to settings.ACS_COMPANY_ID."
        ),
    )
    acs_company_password = models.CharField(
        _("ACS Company Password"),
        max_length=128,
        blank=True,
        default="",
        help_text=_(
            "ACS company password. "
            "Empty falls back to settings.ACS_COMPANY_PASSWORD."
        ),
    )
    acs_user_id = models.CharField(
        _("ACS User ID"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "ACS API user identifier. Empty falls back to settings.ACS_USER_ID."
        ),
    )
    acs_user_password = models.CharField(
        _("ACS User Password"),
        max_length=128,
        blank=True,
        default="",
        help_text=_(
            "ACS API user password. "
            "Empty falls back to settings.ACS_USER_PASSWORD."
        ),
    )
    acs_billing_code = models.CharField(
        _("ACS Billing Code"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "ACS billing code (e.g. 'ΑΚ12345678'). "
            "Empty falls back to settings.ACS_BILLING_CODE."
        ),
    )
    acs_station_origin = models.CharField(
        _("ACS Station Origin"),
        max_length=8,
        blank=True,
        default="",
        help_text=_(
            "ACS origin station code (up to 8 chars). "
            "Empty falls back to settings.ACS_STATION_ORIGIN."
        ),
    )

    # === Shipping — BoxNow (secrets) ===

    box_now_client_id = models.CharField(
        _("BoxNow Client ID"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "BoxNow OAuth2 client ID. "
            "Empty falls back to settings.BOXNOW_CLIENT_ID."
        ),
    )
    box_now_client_secret = models.CharField(
        _("BoxNow Client Secret"),
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "BoxNow OAuth2 client secret. "
            "Empty falls back to settings.BOXNOW_CLIENT_SECRET."
        ),
    )
    box_now_warehouse_id = models.CharField(
        _("BoxNow Warehouse ID"),
        max_length=32,
        blank=True,
        default="",
        help_text=_(
            "BoxNow warehouse identifier. "
            "Empty falls back to settings.BOXNOW_WAREHOUSE_ID."
        ),
    )
    box_now_notify_phone = models.CharField(
        _("BoxNow Notify Phone"),
        max_length=32,
        blank=True,
        default="",
        help_text=_(
            "Phone number for BoxNow shipment notifications. "
            "Empty falls back to settings.BOXNOW_NOTIFY_PHONE."
        ),
    )
    box_now_webhook_secret = models.CharField(
        _("BoxNow Webhook Secret"),
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "HMAC secret for verifying BoxNow webhook deliveries — "
            "per-tenant, since each tenant holds their own BoxNow "
            "contract. Empty falls back to "
            "settings.BOXNOW_WEBHOOK_SECRET."
        ),
    )

    # === Agentic Commerce (secret) ===

    acp_bearer_token = models.CharField(
        _("ACP Bearer Token"),
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "Bearer token the agent platform uses on this tenant's "
            "/acp/* checkout surface (served to the agent gateway only "
            "via the internal-token-gated tenant resolve endpoint). "
            "Per-tenant — a token minted for one store must never "
            "drive another store's checkout. Empty disables ACP for "
            "this tenant."
        ),
    )
    agent_stripe_delegated_enabled = models.BooleanField(
        _("Agent Stripe Delegated Payments"),
        default=False,
        help_text=_(
            "Allow AI-agent platforms to complete Stripe payments "
            "server-side with SharedPaymentTokens on this store. "
            "Requires the tenant's Stripe account to be enrolled in "
            "Stripe Agentic Commerce."
        ),
    )
    chat_api_key = models.CharField(
        _("Chat API Key"),
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "LLM API key for the storefront chat assistant (served to "
            "the agent gateway only via the internal-token-gated "
            "tenant resolve endpoint). Each tenant brings their own "
            "key — own quota, consent, and billing. Never expose to "
            "the browser. Empty disables the chat assistant for this "
            "tenant."
        ),
    )

    # Audit trail for the platform control plane (plan changes,
    # suspend/activate, credential edits) — so Unfold's History tab
    # (LogEntry, populated by the admin lifecycle actions) can be
    # cross-checked against actual field-level diffs. ``tenant`` is
    # SHARED_APPS-only (public schema), and so is AUTH_USER_MODEL, so
    # unlike the tenant-schema shipment/Product histories this is a
    # same-schema FK — no ``user_db_constraint=False`` needed here.
    # Excludes the live credential/secret fields: duplicating a
    # merchant's Stripe/ACS/BoxNow/Viva secret into every historical
    # snapshot (one per save — including every suspend/activate) would
    # sprawl plaintext secrets into a table with no dedicated retention
    # or access policy of its own.
    history = HistoricalRecords(
        excluded_fields=[
            "stripe_secret_key",
            "meta_capi_access_token",
            "viva_wallet_api_key",
            "viva_wallet_client_secret",
            "viva_wallet_webhook_verification_key",
            "acs_api_key",
            "acs_company_password",
            "acs_user_password",
            "box_now_client_secret",
            "box_now_webhook_secret",
            "acp_bearer_token",
            "chat_api_key",
        ],
    )

    auto_create_schema = True

    # Schema names that may never be deleted through normal paths.
    # Deletion of these tenants would destroy the platform itself.
    _PROTECTED_SCHEMAS = frozenset({"public", "webside"})

    def delete(
        self, using=None, keep_parents=False, *, force_drop: bool = False
    ):
        """Block deletion of protected tenants.

        Raises ``ValidationError`` when called on a tenant whose
        ``schema_name`` is in ``_PROTECTED_SCHEMAS``. All other tenants
        pass through to the django-tenants ``TenantMixin.delete()``
        which respects the ``force_drop`` kwarg to optionally drop the
        Postgres schema.

        Parameters
        ----------
        force_drop:
            When True the underlying Postgres schema is dropped before
            the row is removed. Defaults to False so that accidental
            row deletion does not silently destroy tenant data.
        """
        if self.schema_name in self._PROTECTED_SCHEMAS:
            raise ValidationError(
                _(
                    "Tenant '%(schema)s' is a protected system tenant "
                    "and cannot be deleted."
                )
                % {"schema": self.schema_name}
            )
        return super().delete(
            using=using, keep_parents=keep_parents, force_drop=force_drop
        )

    class Meta:
        verbose_name = _("Tenant")
        verbose_name_plural = _("Tenants")

    def __str__(self):
        return self.name

    def clean(self) -> None:
        super().clean()
        self._validate_schema_name()
        self._validate_stripe_publishable_key()
        self._validate_stripe_secret_key()
        self._validate_theme_metadata()
        self._validate_allowed_csp_sources()
        self._validate_meta_pixel_id()
        self._validate_tiktok_pixel_id()
        self._validate_ga_tracking_id()
        self._validate_social_urls()
        self._validate_box_now_partner_id()

    def _validate_schema_name(self) -> None:
        # Field validators only run in full_clean()/DRF; mirror the
        # check here so admin actions and direct clean() calls get the
        # same guarantee as the sibling validators.
        try:
            validate_reserved_schema_name(self.schema_name)
        except ValidationError as exc:
            raise ValidationError({"schema_name": exc.messages}) from exc

    def _validate_stripe_publishable_key(self) -> None:
        key = self.stripe_publishable_key
        if not key:
            return
        if not (key.startswith("pk_test_") or key.startswith("pk_live_")):
            raise ValidationError(
                {
                    "stripe_publishable_key": _(
                        "Stripe publishable key must start with "
                        "'pk_test_' or 'pk_live_'. "
                        "Never store secret keys (sk_*) here."
                    )
                }
            )

    def _validate_stripe_secret_key(self) -> None:
        key = self.stripe_secret_key
        if not key:
            return
        if not key.startswith(("sk_live_", "sk_test_", "rk_")):
            raise ValidationError(
                {
                    "stripe_secret_key": _(
                        "Stripe secret key must start with 'sk_live_', "
                        "'sk_test_' or 'rk_' (restricted key)."
                    )
                }
            )
        # Mode cross-check: a live secret paired with a test publishable
        # key (or vice versa) produces confirmations the browser can
        # never complete — fail loudly at save time instead.
        pub = self.stripe_publishable_key
        if pub:
            secret_live = key.startswith("sk_live_")
            pub_live = pub.startswith("pk_live_")
            if key.startswith(("sk_live_", "sk_test_")) and (
                secret_live != pub_live
            ):
                raise ValidationError(
                    {
                        "stripe_secret_key": _(
                            "Stripe key mode mismatch: the secret key "
                            "and the publishable key must both be live "
                            "or both be test."
                        )
                    }
                )

    def _validate_theme_metadata(self) -> None:
        # Field validators only run in full_clean()/DRF; mirror the
        # check here so admin actions and direct clean() calls get the
        # same guarantee as the sibling validators.
        try:
            validate_theme_metadata(self.theme_metadata)
        except ValidationError as exc:
            raise ValidationError({"theme_metadata": exc.messages}) from exc

    def _validate_allowed_csp_sources(self) -> None:
        sources = self.allowed_csp_sources
        if not sources:
            return
        if not isinstance(sources, list):
            raise ValidationError(
                {
                    "allowed_csp_sources": _(
                        "allowed_csp_sources must be a list of strings."
                    )
                }
            )
        _VALID_PREFIXES = (
            "https://",
            "http://localhost",
            "wss://",
        )
        bad = [
            s
            for s in sources
            if not isinstance(s, str)
            or not any(s.startswith(p) for p in _VALID_PREFIXES)
        ]
        if bad:
            raise ValidationError(
                {
                    "allowed_csp_sources": _(
                        "Each CSP source must start with 'https://', "
                        "'http://localhost', or 'wss://'. "
                        "Invalid entries: %(bad)s"
                    )
                    % {"bad": ", ".join(str(b) for b in bad)}
                }
            )

    def _validate_meta_pixel_id(self) -> None:
        """Meta Pixel IDs are numeric strings only."""
        value = self.meta_pixel_id
        if not value:
            return
        if not re.fullmatch(r"\d+", value):
            raise ValidationError(
                {
                    "meta_pixel_id": _(
                        "Meta Pixel ID must contain digits only "
                        "(e.g. '123456789012345')."
                    )
                }
            )

    def _validate_tiktok_pixel_id(self) -> None:
        """TikTok Pixel IDs are alphanumeric strings only."""
        value = self.tiktok_pixel_id
        if not value:
            return
        if not re.fullmatch(r"[A-Za-z0-9]+", value):
            raise ValidationError(
                {
                    "tiktok_pixel_id": _(
                        "TikTok Pixel ID must be alphanumeric "
                        "(e.g. 'C0ABCDEFGH123')."
                    )
                }
            )

    def _validate_ga_tracking_id(self) -> None:
        """GA tracking IDs must start with G- (GA4) or UA- (Universal)."""
        value = self.ga_tracking_id
        if not value:
            return
        if not (value.startswith("G-") or value.startswith("UA-")):
            raise ValidationError(
                {
                    "ga_tracking_id": _(
                        "Google Analytics tracking ID must start with "
                        "'G-' (GA4) or 'UA-' (Universal Analytics)."
                    )
                }
            )

    _SOCIAL_URL_FIELDS = (
        "socials_discord",
        "socials_facebook",
        "socials_instagram",
        "socials_pinterest",
        "socials_reddit",
        "socials_tiktok",
        "socials_twitter",
        "socials_youtube",
    )

    def _validate_social_urls(self) -> None:
        """All social URL fields must use https:// when non-empty."""
        errors: dict[str, list[str]] = {}
        for field_name in self._SOCIAL_URL_FIELDS:
            value = getattr(self, field_name, "")
            if value and not value.startswith("https://"):
                errors[field_name] = [
                    _("Social URL must use https://. Got: %(value)s")
                    % {"value": value}
                ]
        if errors:
            raise ValidationError(errors)

    def _validate_box_now_partner_id(self) -> None:
        """BoxNow partner ID must be digits only when set."""
        value = self.box_now_partner_id
        if not value:
            return
        if not re.fullmatch(r"\d+", value):
            raise ValidationError(
                {
                    "box_now_partner_id": _(
                        "BoxNow Partner ID must contain digits only."
                    )
                }
            )


class TenantDomain(DomainMixin):
    class Meta:
        verbose_name = _("Tenant Domain")
        verbose_name_plural = _("Tenant Domains")

    def __str__(self):
        return self.domain


class TenantMembershipRole(models.TextChoices):
    """Role a user holds inside a specific tenant.

    The global ``User.is_staff`` / ``is_superuser`` flags keep their
    platform-wide meaning (platform operators who manage Tenant rows in
    the public-schema admin). These per-tenant roles govern what a user
    can do *inside* a tenant they belong to:

    - MEMBER  — RESERVED, no longer issued. Customers are scoped to a
                store by living in that store's schema, not by a
                membership row; the rows this role once carried were
                removed in migration 0015. See tenant/membership.py.
    - STAFF   — can view the tenant's operational admin (orders,
                products) but cannot change tenant settings or invite
                other staff.
    - ADMIN   — full admin within the tenant (settings, team).
    - OWNER   — same as ADMIN plus cannot be demoted/removed by other
                admins; the tenant must always have at least one owner.
    """

    MEMBER = "member", _("Member")
    STAFF = "staff", _("Staff")
    ADMIN = "admin", _("Admin")
    OWNER = "owner", _("Owner")


class UserTenantMembership(TimeStampMixinModel):
    """Join between a PLATFORM-PUBLIC staff identity and a tenant.

    This table is a STAFF grant, not a customer roster. It lives in the
    public schema and its ``user`` FK targets ``public.user_useraccount``,
    so only a public identity can hold a row — which is exactly the set
    of people who operate stores. Shoppers live in their tenant's own
    schema and hold nothing here.

    ``UserAccount`` lives in SHARED_APPS — there is one platform-wide
    identity per email. Membership in a tenant is an explicit row here,
    so the same person can have a ``webside`` membership as MEMBER and
    a ``tenant-b`` membership as ADMIN without duplicating the user
    record. Any API or admin path that is tenant-scoped MUST verify an
    active membership for the requesting user + the current
    ``connection.tenant`` — otherwise a user authenticated on one
    tenant's domain could read another tenant's data by swapping the
    ``X-Forwarded-Host`` header.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tenant_memberships",
        verbose_name=_("User"),
    )
    tenant = models.ForeignKey(
        "tenant.Tenant",
        on_delete=models.CASCADE,
        related_name="user_memberships",
        verbose_name=_("Tenant"),
    )
    role = models.CharField(
        _("Role"),
        max_length=20,
        choices=TenantMembershipRole.choices,
        default=TenantMembershipRole.MEMBER,
    )
    is_active = models.BooleanField(_("Active"), default=True)

    class Meta:
        verbose_name = _("User Tenant Membership")
        verbose_name_plural = _("User Tenant Memberships")
        constraints = [
            models.UniqueConstraint(
                fields=["user", "tenant"],
                name="unique_user_tenant_membership",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "is_active"]),
            models.Index(fields=["user", "is_active"]),
        ]

    def __str__(self):
        return f"{self.user} @ {self.tenant} ({self.role})"

    @property
    def can_manage_tenant(self) -> bool:
        """True if this membership may change tenant-level settings."""
        return self.role in (
            TenantMembershipRole.ADMIN,
            TenantMembershipRole.OWNER,
        )

    @property
    def is_tenant_staff(self) -> bool:
        """True if this membership grants access to the tenant admin."""
        return self.role in (
            TenantMembershipRole.STAFF,
            TenantMembershipRole.ADMIN,
            TenantMembershipRole.OWNER,
        )


class PlatformStaffToken(AbstractAuthToken):
    """API token for a PLATFORM identity — the staff sibling of knox's.

    ``knox.AuthToken`` is in TENANT_APPS only: a customer token is a
    per-schema row, which is what makes it structurally un-replayable
    across stores. Staff are the opposite case — public-schema
    identities operating on tenant data — so their tokens live in the
    ONE app that is SHARED-only (this one), giving the mirror-image
    guarantee: the table exists only in ``public`` and its user FK can
    only ever reference ``public.user_useraccount``.

    A concrete subclass rather than ``KNOX_TOKEN_MODEL`` because that
    setting is a swap (one global model, like ``AUTH_USER_MODEL``) —
    it cannot ADD a token model. Verified against knox 5.1.0, whose
    ``AbstractAuthToken`` is the documented customization path.

    Consumed exclusively by
    ``tenant.api_tokens.PlatformStaffTokenAuthentication`` — never by
    knox's own authentication class, which only queries
    ``get_token_model()``. See ``docs/api-staff-identity.md``.
    """

    # Redeclared only to give the reverse accessor its own name:
    # ``AbstractAuthToken.user`` hardcodes ``related_name=
    # 'auth_token_set'``, which ``knox.AuthToken`` already claims.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="platform_staff_tokens",
        verbose_name=_("User"),
    )

    class Meta:
        verbose_name = _("Platform Staff Token")
        verbose_name_plural = _("Platform Staff Tokens")

    def __str__(self):
        return f"staff:{self.token_key} : {self.user}"
