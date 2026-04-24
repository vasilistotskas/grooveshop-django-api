from __future__ import annotations

from django.conf import settings
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

    auto_create_schema = True

    class Meta:
        verbose_name = _("Tenant")
        verbose_name_plural = _("Tenants")

    def __str__(self):
        return self.name


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
