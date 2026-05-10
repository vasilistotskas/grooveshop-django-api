"""Tenant admin with suspension / destruction lifecycle actions.

Lifecycle model
---------------
ACTIVE  --[suspend_tenants]--> SUSPENDED  --[activate_tenants]--> ACTIVE
SUSPENDED (>24 h) --[destroy_tenants]--> DELETED

Safety invariants:
- ``public`` and ``webside`` tenants are protected: suspend, activate,
  and destroy actions skip them with a warning message.
- Destroy requires the tenant to be suspended (``is_active=False``)
  AND ``suspended_at`` to be at least 24 hours in the past. This
  prevents fat-finger destruction immediately after suspension.
- Destroy calls ``tenant.delete(force_drop=True)`` which drops the
  Postgres schema and then removes the row. This is irreversible.
- The default Django ``delete_selected`` bulk action is disabled so
  operators cannot bypass our safety rails via the standard delete path.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib import admin, messages
from django.db import connection
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.decorators import action
from unfold.enums import ActionVariant

from tenant.models import Tenant, TenantDomain, UserTenantMembership

# Schemas that can never be suspended, activated, or destroyed via the
# admin. Destroying these would break the platform.
_PROTECTED = frozenset({"public", "webside"})

# Minimum time a tenant must be suspended before it can be destroyed.
_SUSPEND_COOLDOWN = timedelta(hours=24)


class TenantDomainInline(admin.TabularInline):
    model = TenantDomain
    extra = 1


@admin.register(Tenant)
class TenantAdmin(ModelAdmin):
    list_display = [
        "name",
        "slug",
        "schema_name",
        "plan",
        "is_active",
        "suspended_at",
        "created_at",
    ]
    list_filter = ["is_active", "plan"]
    search_fields = ["name", "slug", "owner_email"]
    readonly_fields = [
        "schema_name",
        "uuid",
        "created_at",
        "updated_at",
        "suspended_at",
    ]
    inlines = [TenantDomainInline]

    # Disable the default bulk-delete action — use our explicit
    # suspend / destroy actions instead so safety checks always run.
    actions = ["suspend_tenants", "activate_tenants", "destroy_tenants"]

    fieldsets = [
        (
            None,
            {
                "fields": [
                    "name",
                    "slug",
                    "schema_name",
                    "owner_email",
                    "is_active",
                    "suspended_at",
                    "uuid",
                ]
            },
        ),
        (
            "Plan & Billing",
            {"fields": ["plan", "paid_until", "stripe_connect_account_id"]},
        ),
        (
            "Branding",
            {
                "fields": [
                    "store_name",
                    "store_description",
                    "default_locale",
                    "default_currency",
                    "logo_light_url",
                    "logo_dark_url",
                    "favicon_url",
                ]
            },
        ),
        (
            "Theme",
            {
                "fields": [
                    "primary_color",
                    "neutral_color",
                    "accent_hex",
                    "success_hex",
                    "warning_hex",
                    "error_hex",
                    "info_hex",
                    "theme_preset",
                    "theme_metadata",
                ]
            },
        ),
        (
            "Features",
            {"fields": ["loyalty_enabled", "blog_enabled"]},
        ),
        (
            "Analytics",
            {
                "fields": [
                    "meta_pixel_id",
                    "ga_tracking_id",
                    "meta_capi_access_token",
                    "meta_capi_dataset_id",
                ],
                "classes": ["collapse"],
            },
        ),
        (
            "Social Links",
            {
                "fields": [
                    "socials_discord",
                    "socials_facebook",
                    "socials_instagram",
                    "socials_pinterest",
                    "socials_reddit",
                    "socials_tiktok",
                    "socials_twitter",
                    "socials_youtube",
                ],
                "classes": ["collapse"],
            },
        ),
        (
            "Email",
            {
                "fields": [
                    "from_email",
                    "contact_email",
                ],
                "classes": ["collapse"],
            },
        ),
        (
            "Authentication",
            {
                "fields": ["totp_issuer"],
                "classes": ["collapse"],
            },
        ),
        (
            "Bot Protection",
            {
                "fields": [
                    "turnstile_site_key",
                    "turnstile_secret_key",
                ],
                "classes": ["collapse"],
            },
        ),
        (
            "Security",
            {
                "fields": ["allowed_csp_sources"],
                "classes": ["collapse"],
            },
        ),
        (
            "Payments — Viva Wallet",
            {
                "fields": [
                    "viva_wallet_merchant_id",
                    "viva_wallet_api_key",
                    "viva_wallet_client_id",
                    "viva_wallet_client_secret",
                    "viva_wallet_webhook_verification_key",
                ],
                "classes": ["collapse"],
            },
        ),
        (
            "Shipping — ACS",
            {
                "fields": [
                    "acs_api_key",
                    "acs_company_id",
                    "acs_company_password",
                    "acs_user_id",
                    "acs_user_password",
                    "acs_billing_code",
                    "acs_station_origin",
                ],
                "classes": ["collapse"],
            },
        ),
        (
            "Shipping — BoxNow",
            {
                "fields": [
                    "box_now_partner_id",
                    "box_now_client_id",
                    "box_now_client_secret",
                    "box_now_warehouse_id",
                    "box_now_notify_phone",
                ],
                "classes": ["collapse"],
            },
        ),
        (
            "Stripe",
            {
                "fields": ["stripe_publishable_key"],
                "classes": ["collapse"],
            },
        ),
        (
            "Timestamps",
            {
                "fields": ["created_at", "updated_at"],
                "classes": ["collapse"],
            },
        ),
    ]

    def has_module_permission(self, request):
        return connection.schema_name == "public"

    def get_actions(self, request):
        """Remove the default delete_selected action entirely."""
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    # ------------------------------------------------------------------
    # Action A — Suspend
    # ------------------------------------------------------------------

    @action(
        description=str(_("Suspend selected tenants")),
        variant=ActionVariant.WARNING,
        icon="pause_circle",
    )
    def suspend_tenants(self, request, queryset):
        """Set ``is_active=False`` and stamp ``suspended_at``.

        Reversible via ``activate_tenants``. Does not touch the
        Postgres schema or any data. Skips protected schemas.
        """
        now = timezone.now()
        skipped = []
        suspended = []

        for tenant in queryset:
            if tenant.schema_name in _PROTECTED:
                skipped.append(tenant.name)
                continue
            if tenant.is_active or tenant.suspended_at is None:
                # Only stamp suspended_at on the *first* suspension —
                # re-suspending an already-suspended tenant must not
                # reset the cooldown timer.
                update_fields = ["is_active"]
                tenant.is_active = False
                if tenant.suspended_at is None:
                    tenant.suspended_at = now
                    update_fields.append("suspended_at")
                tenant.save(update_fields=update_fields)
                suspended.append(tenant.name)

        if skipped:
            self.message_user(
                request,
                _("Skipped protected tenants: %(names)s")
                % {"names": ", ".join(skipped)},
                level=messages.WARNING,
            )
        if suspended:
            self.message_user(
                request,
                _("Suspended %(count)d tenant(s): %(names)s")
                % {"count": len(suspended), "names": ", ".join(suspended)},
            )

    # ------------------------------------------------------------------
    # Action — Activate (reverse of suspend)
    # ------------------------------------------------------------------

    @action(
        description=str(_("Activate selected tenants")),
        variant=ActionVariant.SUCCESS,
        icon="play_circle",
    )
    def activate_tenants(self, request, queryset):
        """Re-activate a suspended tenant. Clears ``suspended_at``.

        Skips protected schemas.
        """
        skipped = []
        activated = []

        for tenant in queryset:
            if tenant.schema_name in _PROTECTED:
                skipped.append(tenant.name)
                continue
            tenant.is_active = True
            tenant.suspended_at = None
            tenant.save(update_fields=["is_active", "suspended_at"])
            activated.append(tenant.name)

        if skipped:
            self.message_user(
                request,
                _("Skipped protected tenants: %(names)s")
                % {"names": ", ".join(skipped)},
                level=messages.WARNING,
            )
        if activated:
            self.message_user(
                request,
                _("Activated %(count)d tenant(s): %(names)s")
                % {"count": len(activated), "names": ", ".join(activated)},
            )

    # ------------------------------------------------------------------
    # Action B — Permanently destroy
    # ------------------------------------------------------------------

    @action(
        description=str(_("Permanently destroy tenant + drop schema")),
        variant=ActionVariant.DANGER,
        icon="delete_forever",
    )
    def destroy_tenants(self, request, queryset):
        """Drop the Postgres schema and remove the Tenant row.

        Safety gates (all must pass for a tenant to be destroyed):

        1. Schema is not in ``_PROTECTED`` (public / webside).
        2. Tenant is suspended (``is_active=False``).
        3. ``suspended_at`` is at least 24 hours in the past —
           prevents accidental destruction immediately after suspension.

        This action is **irreversible**. The Postgres schema and all
        tenant data are permanently gone after this runs.
        """
        now = timezone.now()
        skipped_protected = []
        skipped_not_suspended = []
        skipped_cooldown = []
        destroyed = []

        for tenant in queryset:
            if tenant.schema_name in _PROTECTED:
                skipped_protected.append(tenant.name)
                continue

            if tenant.is_active or tenant.suspended_at is None:
                skipped_not_suspended.append(tenant.name)
                continue

            age = now - tenant.suspended_at
            if age < _SUSPEND_COOLDOWN:
                remaining_minutes = int(
                    (_SUSPEND_COOLDOWN - age).total_seconds() // 60
                )
                skipped_cooldown.append(
                    f"{tenant.name} ({remaining_minutes} min remaining)"
                )
                continue

            # All gates passed — drop schema + row.
            try:
                tenant.delete(force_drop=True)
                destroyed.append(tenant.name)
            except Exception as exc:  # noqa: BLE001
                self.message_user(
                    request,
                    _("Error destroying %(name)s: %(error)s")
                    % {"name": tenant.name, "error": str(exc)},
                    level=messages.ERROR,
                )

        if skipped_protected:
            self.message_user(
                request,
                _("Skipped protected tenants (cannot destroy): %(names)s")
                % {"names": ", ".join(skipped_protected)},
                level=messages.WARNING,
            )
        if skipped_not_suspended:
            self.message_user(
                request,
                _("Skipped non-suspended tenants (suspend first): %(names)s")
                % {"names": ", ".join(skipped_not_suspended)},
                level=messages.WARNING,
            )
        if skipped_cooldown:
            self.message_user(
                request,
                _("Skipped tenants still within 24-hour cooldown: %(names)s")
                % {"names": ", ".join(skipped_cooldown)},
                level=messages.WARNING,
            )
        if destroyed:
            self.message_user(
                request,
                _("Permanently destroyed %(count)d tenant(s): %(names)s")
                % {"count": len(destroyed), "names": ", ".join(destroyed)},
                level=messages.SUCCESS,
            )


@admin.register(TenantDomain)
class TenantDomainAdmin(ModelAdmin):
    list_display = ["domain", "tenant", "is_primary"]
    list_filter = ["is_primary"]
    search_fields = ["domain"]

    def has_module_permission(self, request):
        return connection.schema_name == "public"


@admin.register(UserTenantMembership)
class UserTenantMembershipAdmin(ModelAdmin):
    list_display = ["user", "tenant", "role", "is_active", "created_at"]
    list_filter = ["role", "is_active", "tenant"]
    search_fields = ["user__email", "user__username", "tenant__name"]
    autocomplete_fields = ["user", "tenant"]
    readonly_fields = ["created_at", "updated_at"]

    def has_module_permission(self, request):
        # Membership admin is a platform-wide surface — like Tenant and
        # TenantDomain, it must not leak into tenant-scoped admin.
        return connection.schema_name == "public"
