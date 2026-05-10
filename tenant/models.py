from __future__ import annotations

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_tenants.models import DomainMixin, TenantMixin

from core.models import TimeStampMixinModel, UUIDModel


class TenantPlan(models.TextChoices):
    TRIAL = "trial", _("Trial")
    BASIC = "basic", _("Basic")
    PRO = "pro", _("Pro")
    ENTERPRISE = "enterprise", _("Enterprise")


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

    # Plan / billing
    plan = models.CharField(
        _("Plan"),
        max_length=20,
        choices=TenantPlan.choices,
        default=TenantPlan.TRIAL,
    )
    paid_until = models.DateField(_("Paid Until"), null=True, blank=True)

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
        _("Theme Metadata"), default=dict, blank=True
    )

    # Feature flags
    loyalty_enabled = models.BooleanField(_("Loyalty Enabled"), default=False)
    blog_enabled = models.BooleanField(_("Blog Enabled"), default=True)

    # Stripe Connect
    stripe_connect_account_id = models.CharField(
        _("Stripe Connect Account ID"),
        max_length=255,
        blank=True,
        default="",
    )

    # Public Stripe key — safe to expose to unauthenticated callers.
    # Use the per-tenant ``pk_live_*`` / ``pk_test_*`` key here.
    # Empty string means "fall back to the platform-wide key from
    # settings.STRIPE_PUBLISHABLE_KEY (NUXT_PUBLIC_STRIPE_KEY)."
    stripe_publishable_key = models.CharField(
        _("Stripe publishable key"),
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "Per-tenant Stripe publishable key (pk_live_* or pk_test_*). "
            "This value is public by design and is safe to expose to "
            "unauthenticated callers. Leave blank to use the platform-wide key."
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

    # === Bot Protection ===

    turnstile_site_key = models.CharField(
        _("Turnstile Site Key"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "Cloudflare Turnstile site key (public). "
            "Shown to browsers for the CAPTCHA widget. "
            "Empty → Turnstile widget is disabled for this tenant."
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

    # === Bot Protection (secret) ===

    turnstile_secret_key = models.CharField(
        _("Turnstile Secret Key"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "Cloudflare Turnstile secret key (server-side validation). "
            "Never expose to the browser. "
            "Empty falls back to settings.TURNSTILE_SECRET_KEY."
        ),
    )

    auto_create_schema = True

    # Schema names that may never be deleted through normal paths.
    # Deletion of these tenants would destroy the platform itself.
    _PROTECTED_SCHEMAS = frozenset({"public", "webside"})

    def delete(self, *args, force_drop: bool = False, **kwargs):
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
        return super().delete(*args, force_drop=force_drop, **kwargs)

    class Meta:
        verbose_name = _("Tenant")
        verbose_name_plural = _("Tenants")

    def __str__(self):
        return self.name

    def clean(self) -> None:
        super().clean()
        self._validate_stripe_publishable_key()
        self._validate_allowed_csp_sources()
        self._validate_meta_pixel_id()
        self._validate_ga_tracking_id()
        self._validate_social_urls()
        self._validate_box_now_partner_id()

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

    - MEMBER  — ordinary shopper, no admin surface.
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
    """Join between a global user and a tenant they can access.

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
