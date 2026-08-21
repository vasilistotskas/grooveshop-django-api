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

from tenant.models import (
    Tenant,
    TenantDomain,
    TenantMembershipRole,
    UserTenantMembership,
)

# Schemas that can never be suspended, activated, or destroyed via the
# admin. Destroying these would break the platform.
_PROTECTED = frozenset({"public", "webside"})

# Minimum time a tenant must be suspended before it can be destroyed.
_SUSPEND_COOLDOWN = timedelta(hours=24)


class TenantDomainInline(admin.TabularInline):
    model = TenantDomain
    extra = 1


def self_service_tenant(request):
    """The store a non-platform operator is administering, else None.

    Returns None for platform superusers (who are unrestricted, and
    bypass permission checks anyway) and on the public schema, so every
    caller can read a non-None result as "restrict to this one store".

    This is the object-scoping half of role-derived permissions:
    ``TenantRolePermissionBackend`` decides that a store ADMIN/OWNER may
    reach ``Tenant`` at all, and this decides that they may only reach
    THEIR row. Without it, ``change_tenant`` would be a licence to edit
    every merchant's payment credentials.
    """
    # ``request.user`` is guaranteed on any real admin request
    # (AuthenticationMiddleware), but not on a bare RequestFactory one —
    # and a missing user must not raise from inside a formfield hook.
    user = getattr(request, "user", None)
    if getattr(user, "is_superuser", False):
        return None

    from tenant.membership import get_current_tenant  # noqa: PLC0415

    return get_current_tenant()


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

    def get_queryset(self, request):
        """A store operator sees only their own row."""
        qs = super().get_queryset(request)
        scope = self_service_tenant(request)
        if scope is None:
            return qs
        return qs.filter(pk=scope.pk)

    def get_readonly_fields(self, request, obj=None):
        """Everything except the merchant's own settings.

        An ALLOWLIST (``TENANT_SELF_EDITABLE_FIELDS``), so a field added
        to ``Tenant`` later is read-only until someone decides it is the
        merchant's to change. A denylist would silently expose it.
        """
        readonly = super().get_readonly_fields(request, obj)
        if self_service_tenant(request) is None:
            return readonly

        from tenant.role_scopes import (  # noqa: PLC0415
            TENANT_SELF_EDITABLE_FIELDS,
        )

        return tuple(
            field.name
            for field in self.model._meta.fields
            if field.name not in TENANT_SELF_EDITABLE_FIELDS
        )

    def has_add_permission(self, request):
        """Only the platform creates stores."""
        if self_service_tenant(request) is not None:
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        """Only the platform destroys stores."""
        if self_service_tenant(request) is not None:
            return False
        return super().has_delete_permission(request, obj)

    def get_inlines(self, request, obj):
        """Domains are platform-controlled — they steer routing and TLS."""
        if self_service_tenant(request) is not None:
            return []
        return super().get_inlines(request, obj)

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
                    "tiktok_pixel_id",
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
            "Agentic Commerce",
            {
                "fields": [
                    "chat_api_key",
                    "acp_bearer_token",
                    "agent_stripe_delegated_enabled",
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
                    "viva_wallet_source_code",
                    "viva_wallet_live_mode",
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
                    "box_now_webhook_secret",
                ],
                "classes": ["collapse"],
            },
        ),
        (
            "Payments — Stripe",
            {
                "fields": [
                    "stripe_publishable_key",
                    "stripe_secret_key",
                ],
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
        """Public always; a tenant host only for its own ADMIN/OWNER.

        This was public-only, which made "ADMIN — full admin within the
        tenant (settings, team)" unreachable: a merchant could not edit
        their own store name, branding or payment credentials. The grant
        comes from the role (``TenantRolePermissionBackend`` withholds
        it from STAFF), and ``get_queryset`` plus ``get_readonly_fields``
        confine it to their own row and their own fields.
        """
        if connection.schema_name == "public":
            return True
        return request.user.has_perm("tenant.change_tenant")

    def get_actions(self, request):
        """Drop delete_selected, and platform lifecycle for merchants.

        Suspend/activate/destroy are the platform's levers; leaving them
        exposed would let a merchant suspend — or destroy — their own
        store from the store admin.
        """
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        if self_service_tenant(request) is not None:
            for name in (
                "suspend_tenants",
                "activate_tenants",
                "destroy_tenants",
            ):
                actions.pop(name, None)
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
        """Public always; a tenant host only for its own ADMIN/OWNER.

        This used to be public-only, which made "ADMIN — full admin
        within the tenant (settings, team)" unimplementable: a store
        owner could not add or remove their own staff. The grant now
        comes from the role (``TenantRolePermissionBackend`` withholds
        it from STAFF), and ``get_queryset`` confines it to that store's
        own rows.
        """
        if connection.schema_name == "public":
            return True
        return request.user.has_perm("tenant.view_usertenantmembership")

    def get_queryset(self, request):
        """A store operator sees only their own store's team."""
        qs = super().get_queryset(request)
        scope = self_service_tenant(request)
        if scope is None:
            return qs
        return qs.filter(tenant=scope)

    def has_change_permission(self, request, obj=None):
        """Only an OWNER may edit an OWNER row.

        "OWNER — same as ADMIN plus cannot be demoted/removed by other
        admins" (TenantMembershipRole). Without this an ADMIN could
        demote the owner and take the store.
        """
        if not super().has_change_permission(request, obj):
            return False
        return self._may_act_on(request, obj)

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        return self._may_act_on(request, obj)

    def _may_act_on(self, request, obj) -> bool:
        scope = self_service_tenant(request)
        if scope is None or obj is None:
            return True
        if obj.role != TenantMembershipRole.OWNER:
            return True

        from tenant.membership import get_membership  # noqa: PLC0415

        membership = get_membership(request.user, scope)
        return (
            membership is not None
            and membership.role == TenantMembershipRole.OWNER
        )

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        """An ADMIN cannot mint an OWNER — only an OWNER can."""
        if db_field.name == "role":
            scope = self_service_tenant(request)
            if scope is not None:
                from tenant.membership import get_membership  # noqa: PLC0415

                membership = get_membership(request.user, scope)
                is_owner = (
                    membership is not None
                    and membership.role == TenantMembershipRole.OWNER
                )
                if not is_owner:
                    kwargs["choices"] = [
                        (value, label)
                        for value, label in db_field.choices
                        if value != TenantMembershipRole.OWNER
                    ]
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Pin the ``user`` FK choices to the PUBLIC schema.

        ``UserAccount`` is mirrored per-schema — an unqualified
        ``UserAccount.objects.all()`` resolves against whatever schema
        the connection is currently pinned to. Membership rows only
        ever reference PUBLIC-schema users (platform identities), so
        the pks captured here are evaluated eagerly, inside
        ``schema_context(public)``, rather than left as a lazy
        queryset that could be (re-)evaluated later against a
        different schema.
        """
        if db_field.name == "user":
            from django.contrib.auth import get_user_model
            from django_tenants.utils import (
                get_public_schema_name,
                schema_context,
            )

            user_model = get_user_model()
            with schema_context(get_public_schema_name()):
                public_user_ids = list(
                    user_model.objects.order_by(
                        user_model.USERNAME_FIELD
                    ).values_list("pk", flat=True)
                )
            kwargs["queryset"] = user_model.objects.filter(
                pk__in=public_user_ids
            )
        elif db_field.name == "tenant":
            # A store operator may only grant membership in THEIR store.
            # Without this the dropdown lists every tenant, and saving
            # one would hand them a foothold in another merchant's admin.
            scope = self_service_tenant(request)
            if scope is not None:
                kwargs["queryset"] = Tenant.objects.filter(pk=scope.pk)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
