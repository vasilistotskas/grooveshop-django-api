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
- Destroy requires an explicit second confirmation (intermediate page)
  before it calls ``tenant.delete(force_drop=True)``, which drops the
  Postgres schema and then removes the row. This is irreversible.
- The default Django ``delete_selected`` bulk action is disabled so
  operators cannot bypass our safety rails via the standard delete path.

Provisioning a NEW tenant (the "New Store" add form) runs the same
``tenant.provisioning`` steps as ``manage.py tenant_create`` — see
``save_related`` below — so the two entry points can never drift.

Lifecycle actions also write ``LogEntry``/``HistoricalRecords`` rows
(``self.log_change``/``self.log_deletions``) so Unfold's History tab and
``Tenant.history`` are meaningful audit trails, not just current state.
"""

from __future__ import annotations

from functools import wraps
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.actions import delete_selected
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from unfold.admin import TabularInline
from unfold.contrib.filters.admin import ChoicesRadioFilter
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from admin.base import BaseModelAdmin
from admin.displays import format_dt, header_two_line
from core.db.encrypted_fields import EncryptedCharField
from tenant.admin_labels import billing_badge, label, plan_badge, status_badge
from tenant.lifecycle import (
    SUSPEND_COOLDOWN,
    activate_tenant,
    destroy_refusal,
    is_protected_tenant,
    suspend_tenant,
)
from tenant.membership import bound_tenant
from tenant.models import (
    SuspendedReason,
    Tenant,
    TenantArchive,
    TenantDomain,
    TenantMembershipRole,
    UserTenantMembership,
)
from tenant.role_scopes import selectable_roles


class TenantDomainInline(TabularInline):
    model = TenantDomain
    extra = 1
    tab = True


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

    from tenant.membership import get_current_tenant

    return get_current_tenant()


def _public_schema_context():
    from django_tenants.utils import (
        get_public_schema_name,
        schema_context,
    )

    return schema_context(get_public_schema_name())


def _platform_secret_fields() -> tuple[str, ...]:
    """Encrypted fields the store's own operators may not change.

    They belong to the platform (the chat and ACP agent credentials, the
    Cloudflare token for a store's own zone), so a store role has no
    business reading them either: a read-only field is still rendered,
    and rendered decrypted.
    """
    from tenant.role_scopes import TENANT_SELF_EDITABLE_FIELDS

    return tuple(
        field.name
        for field in Tenant._meta.fields
        if isinstance(field, EncryptedCharField)
        and field.name not in TENANT_SELF_EDITABLE_FIELDS
    )


def _secret_is_set(field_name: str):
    """A read-only "set / not set" column standing in for a secret."""
    verbose_name = Tenant._meta.get_field(field_name).verbose_name

    @admin.display(boolean=True, description=verbose_name)
    def is_set(self, obj):
        return bool(getattr(obj, field_name))

    return is_set


@admin.register(Tenant)
class TenantAdmin(BaseModelAdmin):
    list_display = [
        "display_store",
        "display_plan",
        "display_status",
        "display_billing_state",
        "display_last_activity",
        "schema_name",
        "created_at",
    ]
    list_display_links = ["display_store"]
    list_filter = [
        ("plan", ChoicesRadioFilter),
        "is_active",
    ]
    search_fields = ["name", "slug", "owner_email"]
    # ``is_active`` changes only through the Suspend / Activate actions
    # (``tenant.lifecycle``), as on the platform API: ticking it in the
    # form skipped the media flush and left ``suspended_at`` behind, a store
    # active by one rule and suspended by another.
    readonly_fields = [
        "schema_name",
        "uuid",
        "created_at",
        "updated_at",
        "is_active",
        "suspended_at",
        "suspended_reason",
        "billing_notice_stage",
        "billing_notice_term",
    ]
    inlines = [TenantDomainInline]

    # Disable the default bulk-delete action — use our explicit
    # suspend / destroy actions instead so safety checks always run.
    actions = [
        "suspend_tenants",
        "activate_tenants",
        "destroy_tenants",
        "provision_stripe_webhook",
        "export_tenant_data_action",
    ]

    @display(description=_("Store"), ordering="name", header=True)
    def display_store(self, obj):
        """Store name over its primary domain, with the store's own mark.

        ``header=True`` renders two lines plus a leading avatar — the
        store's logo when it has one, otherwise its initials. On a
        control plane listing every merchant, the logo is the fastest
        way to identify a row. Domains are prefetched (``get_queryset``).
        """
        primary = next(
            (domain for domain in obj.domains.all() if domain.is_primary),
            None,
        )
        name = obj.store_name or obj.name
        return header_two_line(
            name,
            primary.domain if primary else obj.schema_name,
            image_path=obj.logo_light_url or None,
            squared=True,
        )

    @display(description=_("Plan"), ordering="plan")
    def display_plan(self, obj):
        return plan_badge(obj)

    @display(description=_("Status"), ordering="is_active")
    def display_status(self, obj):
        return status_badge(obj)

    @display(description=_("Billing"))
    def display_billing_state(self, obj):
        """The same classifier the Plan & Billing page renders (see
        ``tenant.billing.billing_state``) — surfaced here too so a
        past-due store is visible without leaving the Tenants list."""
        from tenant.billing import billing_state

        return billing_badge(billing_state(obj, timezone.localdate()))

    @display(description=_("Last order"))
    def display_last_activity(self, obj):
        """The store's latest order, from its ``TenantStatsSnapshot``.

        Read inside the store's schema by ``refresh_stats_snapshot``
        rather than here: this column used to switch schema per row. "—"
        until the first refresh, and for the platform's own row, which
        has no orders.
        """
        snapshot = getattr(obj, "stats_snapshot", None)
        if snapshot is None or snapshot.last_order_at is None:
            return "—"
        return format_dt(snapshot.last_order_at)

    def get_queryset(self, request):
        """A store operator sees only their own row."""
        qs = (
            super()
            .get_queryset(request)
            .select_related("stats_snapshot")
            .prefetch_related("domains")
        )
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

        from tenant.role_scopes import (
            TENANT_SELF_EDITABLE_FIELDS,
        )

        return (
            *(
                field.name
                for field in self.model._meta.fields
                if field.name not in TENANT_SELF_EDITABLE_FIELDS
            ),
            *(f"{name}_is_set" for name in _platform_secret_fields()),
        )

    def get_fieldsets(self, request, obj=None):
        """A store role sees whether a platform secret is set, never it.

        The secret's own field is swapped for its "set / not set" column
        in the fieldset, so its value is never rendered for a store role
        (the platform operator still sees and edits the real field).
        """
        fieldsets = super().get_fieldsets(request, obj)
        if self_service_tenant(request) is None:
            return fieldsets
        secrets = set(_platform_secret_fields())
        return [
            (
                name,
                {
                    **options,
                    "fields": [
                        f"{field}_is_set" if field in secrets else field
                        for field in options["fields"]
                    ],
                },
            )
            for name, options in fieldsets
        ]

    def has_add_permission(self, request):
        """Only the platform creates stores."""
        if self_service_tenant(request) is not None:
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        """Only the platform destroys stores — and never protected ones.

        ``Tenant.delete()`` raises ``ValidationError`` for protected
        schemas, but the admin delete view does not catch it (only
        ``ProtectedError``), so the red delete button on ``public`` /
        ``webside`` was a 500 waiting behind a confirm page. Withhold
        the permission instead: the button disappears and the delete
        view answers 403, mirroring how the bulk actions skip these
        schemas.
        """
        if self_service_tenant(request) is not None:
            return False
        if obj is not None and is_protected_tenant(obj):
            return False
        return super().has_delete_permission(request, obj)

    def get_inlines(self, request, obj):
        """Domains are platform-controlled — they steer routing and TLS."""
        if self_service_tenant(request) is not None:
            return []
        return super().get_inlines(request, obj)

    def save_related(self, request, form, formsets, change):
        """Provision a brand-new tenant the same way ``tenant_create`` does.

        The stock Unfold "add" form only creates the ``Tenant`` row (and,
        via ``TenantMixin.save()``, the Postgres schema). Everything a
        store actually needs to be usable — the ``api.<domain>``
        TenantDomain, the owner's OWNER membership, and the tenant's
        default data — used to only happen on the CLI path
        (``manage.py tenant_create``), so a store created here left its
        owner locked out and realtime/social-login 404ing. See
        ``tenant.provisioning`` for the shared steps.

        Runs in ``save_related`` (not ``save_model``) so the
        ``TenantDomainInline`` primary domain is already persisted —
        ``ensure_api_domain`` needs it to derive the API host. Deferred
        to ``transaction.on_commit`` so a Meilisearch/page_config hiccup
        can never roll back the tenant row itself, and so the
        provisioning queries (some of which switch into the new
        tenant's own schema) only run once the row + inline domains are
        durably committed.

        Guarded to ADD only — editing an existing tenant must never
        re-derive its API domain or re-grant OWNER membership.
        """
        super().save_related(request, form, formsets, change)
        if change:
            return

        tenant_pk = form.instance.pk
        transaction.on_commit(
            lambda: self._provision_new_tenant(request, tenant_pk)
        )

    def _provision_new_tenant(self, request, tenant_pk) -> None:
        from tenant.provisioning import provision_tenant

        try:
            tenant = Tenant.objects.get(pk=tenant_pk)
        except Tenant.DoesNotExist:  # pragma: no cover - defensive
            return

        try:
            result = provision_tenant(tenant)
        except Exception as exc:
            self.message_user(
                request,
                _(
                    "Tenant created, but automatic provisioning failed: "
                    "%(error)s. Provision the API domain, owner "
                    "membership, and defaults manually."
                )
                % {"error": str(exc)},
                level=messages.ERROR,
            )
            return

        parts = []
        if result["api_domain"] is not None:
            parts.append(
                _("API domain %(domain)s created.")
                % {"domain": result["api_domain"]}
            )
        else:
            self.message_user(
                request,
                _(
                    "No primary domain was set — could not derive the "
                    "API domain (api.<domain>). Add a primary domain, "
                    "then add the api.<domain> row manually: without "
                    "it, WebSocket notifications and social login will "
                    "fail."
                ),
                level=messages.WARNING,
            )

        if result["seeding_failures"]:
            self.message_user(
                request,
                _(
                    "The store was created but these defaults did not "
                    "seed: %(steps)s. It is usable, but check the server "
                    "log — a missing Meilisearch index makes EVERY search "
                    "on this store return an error until the nightly sync "
                    "repairs it."
                )
                % {"steps": ", ".join(result["seeding_failures"])},
                level=messages.WARNING,
            )

        membership_result = result["membership"]
        if membership_result is None:
            self.message_user(
                request,
                _(
                    "No UserAccount exists yet for owner %(email)s — "
                    "OWNER membership was skipped. Grant it manually "
                    "once the owner has registered."
                )
                % {"email": tenant.owner_email},
                level=messages.WARNING,
            )
        else:
            _membership, created = membership_result
            parts.append(
                _("OWNER membership %(verb)s for %(email)s.")
                % {
                    "verb": _("granted") if created else _("updated"),
                    "email": tenant.owner_email,
                }
            )

        if parts:
            self.message_user(
                request,
                " ".join(str(part) for part in parts),
                level=messages.INFO,
            )

    # Eight tabs, not seventeen collapsed sections: Unfold gives every
    # tabbed fieldset its own tab, and the tab bar does not wrap.
    fieldsets = [
        (
            _("Store"),
            {
                "classes": ["tab"],
                "fields": [
                    "name",
                    "slug",
                    "schema_name",
                    "owner_email",
                    "is_active",
                    # Here rather than under Features on purpose: it
                    # gates no storefront behaviour, it declares what
                    # KIND of tenant this row is.
                    "is_demo",
                    "is_protected",
                    "is_platform_storefront",
                    # What the store sells — selects the recommendation
                    # preset it starts from.
                    "vertical",
                    "suspended_at",
                    "suspended_reason",
                    "uuid",
                    "created_at",
                    "updated_at",
                ],
            },
        ),
        (
            _("Plan & Billing"),
            {
                "classes": ["tab"],
                "fields": [
                    "plan",
                    "paid_until",
                    "billing_notice_stage",
                    "billing_notice_term",
                    "stripe_connect_account_id",
                ],
            },
        ),
        (
            _("Branding"),
            {
                "classes": ["tab"],
                "fields": [
                    "store_name",
                    "store_description",
                    "store_description_i18n",
                    "default_locale",
                    "available_locales",
                    "default_currency",
                    "logo_light_url",
                    "logo_dark_url",
                    "favicon_url",
                    "primary_color",
                    "neutral_color",
                    "accent_hex",
                    "success_hex",
                    "warning_hex",
                    "error_hex",
                    "info_hex",
                    "theme_preset",
                    "theme_metadata",
                    "socials_discord",
                    "socials_facebook",
                    "socials_instagram",
                    "socials_linkedin",
                    "socials_pinterest",
                    "socials_reddit",
                    "socials_tiktok",
                    "socials_twitter",
                    "socials_youtube",
                ],
            },
        ),
        (
            _("Features"),
            {
                "classes": ["tab"],
                "fields": [
                    "loyalty_enabled",
                    "blog_enabled",
                    "promotions_enabled",
                    "gift_cards_enabled",
                    # Was absent entirely, so the wholesale plan gate
                    # could only be flipped from a shell — every other
                    # plan gate is here.
                    "b2b_enabled",
                    "recommendations_enabled",
                    "agent_commerce_enabled",
                    "agent_hosted_payment_enabled",
                    "agent_stripe_delegated_enabled",
                    "chat_api_key",
                    "acp_bearer_token",
                ],
            },
        ),
        (
            _("Marketing"),
            {
                "classes": ["tab"],
                "fields": [
                    "seo_author",
                    "google_site_verification",
                    "pinterest_domain_verify",
                    "meta_pixel_id",
                    "tiktok_pixel_id",
                    "ga_tracking_id",
                    "google_ads_conversion_id",
                    "google_ads_purchase_label",
                    "google_ads_add_to_cart_label",
                    "google_ads_begin_checkout_label",
                    "google_ads_add_payment_info_label",
                    "google_ads_page_view_label",
                    "meta_capi_access_token",
                    "meta_capi_dataset_id",
                ],
            },
        ),
        (
            _("Email & Security"),
            {
                "classes": ["tab"],
                "fields": [
                    "from_email",
                    "from_email_verified",
                    "contact_email",
                    "totp_issuer",
                    "allowed_csp_sources",
                    "cloudflare_zone_id",
                    "cloudflare_api_token",
                ],
                "description": _(
                    "The Cloudflare fields are only for a store on its own "
                    "domain in its own Cloudflare account. Leave them empty "
                    "on a platform hostname."
                ),
            },
        ),
        (
            _("Payments"),
            {
                "classes": ["tab"],
                "fields": [
                    "viva_wallet_merchant_id",
                    "viva_wallet_api_key",
                    "viva_wallet_client_id",
                    "viva_wallet_client_secret",
                    "viva_wallet_webhook_verification_key",
                    "viva_wallet_source_code",
                    "viva_wallet_live_mode",
                    "stripe_publishable_key",
                    "stripe_secret_key",
                ],
            },
        ),
        (
            _("Shipping"),
            {
                "classes": ["tab"],
                "fields": [
                    "acs_api_key",
                    "acs_company_id",
                    "acs_company_password",
                    "acs_user_id",
                    "acs_user_password",
                    "acs_billing_code",
                    "acs_station_origin",
                    "box_now_partner_id",
                    "box_now_client_id",
                    "box_now_client_secret",
                    "box_now_warehouse_id",
                    "box_now_notify_phone",
                    "box_now_webhook_secret",
                    "box_now_live_mode",
                ],
            },
        ),
        (
            _("Invoicing & myDATA"),
            {
                "classes": ["tab"],
                "fields": [
                    "mydata_user_id",
                    "mydata_subscription_key",
                ],
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

    def get_actions(self, request, *args, **kwargs):
        """Drop delete_selected, and platform lifecycle for merchants.

        Suspend/activate/destroy are the platform's levers; leaving them
        exposed would let a merchant suspend — or destroy — their own
        store from the store admin.
        """
        actions = super().get_actions(request, *args, **kwargs)
        actions.pop("delete_selected", None)
        if self_service_tenant(request) is not None:
            for name in (
                "suspend_tenants",
                "activate_tenants",
                "destroy_tenants",
                # Whole-schema dump written to the private volume — an
                # operator offboarding step, not a merchant self-service
                # download.
                "export_tenant_data_action",
            ):
                actions.pop(name, None)
        return actions

    # ------------------------------------------------------------------
    # Action — Provision Stripe
    # ------------------------------------------------------------------

    @action(
        description=_("Provision Stripe webhook"),
        icon="webhook",
    )
    def provision_stripe_webhook(self, request, queryset):
        """Register the tenant's Stripe API key + webhook endpoint.

        Deliberately NOT stripped for merchants in ``get_actions``:
        pasting a Stripe secret into the Tenant form is exactly the
        moment this needs to run, and until now the only way to run it
        was ``manage.py bootstrap_stripe`` — so a merchant who saved
        their key got no webhook endpoint, dj-stripe never received
        ``payment_intent.succeeded``, and their Stripe orders never
        confirmed, with nothing in the product surfacing why.

        Safe for a merchant to trigger on their own store: the queryset
        is already scoped to their own row, and it only ever touches
        THEIR Stripe account using THEIR key. Idempotent — an existing
        endpoint is reported, not replaced (rotation stays a CLI
        operation, since it needs the old endpoint disabled in the
        Stripe dashboard first).
        """
        from tenant.provisioning import provision_stripe

        level_by_status = {
            "created": messages.SUCCESS,
            "exists": messages.INFO,
            "no_key": messages.WARNING,
            "no_domain": messages.ERROR,
        }

        for tenant in queryset:
            if is_protected_tenant(tenant):
                continue
            try:
                result = provision_stripe(tenant)
            except Exception as exc:
                self.message_user(
                    request,
                    _("%(store)s: Stripe provisioning failed — %(error)s")
                    % {"store": tenant.name, "error": exc},
                    messages.ERROR,
                )
                continue

            self.message_user(
                request,
                f"{tenant.name}: {result['detail']}",
                level_by_status.get(result["status"], messages.INFO),
            )
            if result["status"] == "created":
                self.log_change(
                    request, tenant, str(_("Provisioned Stripe webhook"))
                )

    # ------------------------------------------------------------------
    # Action — Export store data (GDPR art. 28(3)(g) "return")
    # ------------------------------------------------------------------

    @action(
        description=_("Export store data (before destroying)"),
        icon="download",
    )
    def export_tenant_data_action(self, request, queryset):
        """Give the controller the "return" half of their art. 28 choice.

        A processor must delete OR RETURN personal data at the end of
        processing, AT THE CONTROLLER'S CHOICE. Destroying a store used
        to offer only deletion, which answers half the article and takes
        the merchant's own records with it.

        Platform-operator only: the queryset spans stores, and the dump
        is written to the private volume rather than handed to a
        browser, so it is an operator step in the offboarding runbook
        rather than a self-service download.
        """
        from tenant.lifecycle import export_tenant_data

        for tenant in queryset:
            if is_protected_tenant(tenant):
                continue
            try:
                path = export_tenant_data(
                    tenant, actor=getattr(request.user, "email", "") or ""
                )
            except Exception as exc:
                self.message_user(
                    request,
                    _("%(store)s: export failed — %(error)s")
                    % {"store": tenant.name, "error": exc},
                    messages.ERROR,
                )
                continue
            self.message_user(
                request,
                _("%(store)s: data exported to %(path)s")
                % {"store": tenant.name, "path": path},
                messages.SUCCESS,
            )

    # ------------------------------------------------------------------
    # Action A — Suspend
    # ------------------------------------------------------------------

    @action(
        description=_("Suspend selected tenants"),
        variant=ActionVariant.WARNING,
        icon="pause_circle",
    )
    def suspend_tenants(self, request, queryset):
        """Set ``is_active=False`` and stamp ``suspended_at``.

        Reversible via ``activate_tenants``. Does not touch the
        Postgres schema or any data. Skips protected schemas.

        Semantics (first-suspension stamping, cooldown preservation)
        live in ``tenant.lifecycle.suspend_tenant`` — the same code
        path the billing dunning task uses, with a different reason.
        """
        skipped = []
        suspended = []

        for tenant in queryset:
            if is_protected_tenant(tenant):
                skipped.append(tenant.name)
                continue
            if suspend_tenant(tenant, reason=SuspendedReason.MANUAL):
                suspended.append(tenant.name)
                self.log_change(
                    request, tenant, str(_("Suspended via platform admin"))
                )

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
        description=_("Activate selected tenants"),
        variant=ActionVariant.SUCCESS,
        icon="play_circle",
    )
    def activate_tenants(self, request, queryset):
        """Re-activate a suspended tenant. Clears ``suspended_at`` and
        ``suspended_reason`` (via ``tenant.lifecycle.activate_tenant``).

        Skips protected schemas.
        """
        skipped = []
        activated = []

        for tenant in queryset:
            if is_protected_tenant(tenant):
                skipped.append(tenant.name)
                continue
            if activate_tenant(tenant):
                activated.append(tenant.name)
                self.log_change(
                    request, tenant, str(_("Activated via platform admin"))
                )

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
        description=_("Permanently destroy tenant + drop schema"),
        variant=ActionVariant.DANGER,
        icon="delete_forever",
    )
    def destroy_tenants(self, request, queryset):
        """Drop the Postgres schema and remove the Tenant row.

        Safety gates (all must pass for a tenant to be destroyed):

        0. The operator has explicitly confirmed on an intermediate
           page (see ``_destroy_confirmation_page``) — a second,
           deliberate click, not just the one that opened the bulk
           action dropdown.
        1. The tenant is not protected (``is_protected_tenant``).
        2. Tenant is suspended (``is_active=False``).
        3. ``suspended_at`` is at least ``SUSPEND_COOLDOWN`` in the past —
           prevents accidental destruction immediately after suspension.

        Gates 1-3 are ``tenant.lifecycle.destroy_refusal``; they are
        evaluated here only to bucket the operator messages, and
        ``destroy_tenant`` enforces them again itself.

        This action is **irreversible**. The Postgres schema and all
        tenant data are permanently gone after this runs.
        """
        if request.POST.get("destroy_confirmed") != "yes":
            return self._destroy_confirmation_page(request, queryset)

        now = timezone.now()
        skipped_protected = []
        skipped_not_suspended = []
        skipped_cooldown = []
        destroyed = []

        for tenant in queryset:
            refusal = destroy_refusal(tenant, now=now)
            if refusal == "protected":
                skipped_protected.append(tenant.name)
                continue
            if refusal == "not_suspended":
                skipped_not_suspended.append(tenant.name)
                continue
            if refusal == "cooldown":
                remaining = SUSPEND_COOLDOWN - (now - tenant.suspended_at)
                remaining_minutes = int(remaining.total_seconds() // 60)
                skipped_cooldown.append(
                    _("%(name)s (%(minutes)s min remaining)")
                    % {"name": tenant.name, "minutes": remaining_minutes}
                )
                continue

            # All gates passed — drop schema + row.
            try:
                # BEFORE delete(): Django 6.0 replaced the old singular
                # ``log_deletion(request, obj, object_repr)`` with a
                # queryset-based ``log_deletions(request, queryset)``
                # (its own docstring: "must be called before the
                # deletion") — the type stub declares ``queryset`` as
                # an actual ``QuerySet``, so re-query by pk rather than
                # pass ``[tenant]`` (which works at runtime — the base
                # implementation just iterates — but fails ``ty``).
                self.log_deletions(request, Tenant.objects.filter(pk=tenant.pk))
                # ONE destroy path for the admin and the platform API —
                # see tenant.lifecycle.destroy_tenant. It erases the
                # schema, files and search indexes, RETAINS invoices
                # under their statutory period, and records the erasure
                # in TenantArchive.
                from tenant.lifecycle import destroy_tenant

                result = destroy_tenant(
                    tenant, actor=getattr(request.user, "email", "") or ""
                )
                destroyed.append(tenant.name)
                if result["retention_until"]:
                    self.message_user(
                        request,
                        _(
                            "%(name)s: invoices retained until "
                            "%(until)s under the statutory record-keeping "
                            "obligation; everything else erased."
                        )
                        % {
                            "name": tenant.name,
                            "until": result["retention_until"],
                        },
                        level=messages.INFO,
                    )
            except Exception as exc:
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

    def _destroy_confirmation_page(self, request, queryset):
        """An explicit, second "are you sure" step before the drop.

        Rendering an intermediate page from a bulk admin action instead
        of acting immediately is the documented Django extension point
        for two-step confirmations — the same mechanism the built-in
        ``delete_selected`` action uses, and the same shape this
        project already uses for ``ProductAdmin.apply_custom_discount``.
        The page re-POSTs the SAME action with ``destroy_confirmed=yes``,
        so nothing short of that second, deliberate click can drop a
        schema. The cooldown/protected-schema gates in
        ``destroy_tenants`` still run in full on that second POST — this
        only adds a step before them, it does not replace them.

        The changelist URL is resolved against
        ``self.admin_site.name`` rather than hardcoded in the template:
        this action is platform-only (``get_actions`` strips it for
        merchants), reached exclusively through ``PlatformAdminSite``
        (namespace ``platform_admin``), not the merchant ``admin`` site.
        """
        opts = self.model._meta
        site_name = self.admin_site.name
        context = {
            **self.admin_site.each_context(request),
            "title": _("Are you sure?"),
            "queryset": queryset,
            "opts": opts,
            "action_checkbox_name": ACTION_CHECKBOX_NAME,
            "changelist_url": reverse(
                f"{site_name}:{opts.app_label}_{opts.model_name}_changelist"
            ),
        }
        return render(
            request, "admin/tenant/destroy_confirmation.html", context
        )


for _secret in _platform_secret_fields():
    setattr(TenantAdmin, f"{_secret}_is_set", _secret_is_set(_secret))


@admin.register(TenantDomain)
class TenantDomainAdmin(BaseModelAdmin):
    list_display = ["domain", "tenant", "is_primary"]
    list_filter = ["is_primary"]
    search_fields = ["domain"]

    def has_module_permission(self, request):
        return connection.schema_name == "public"


class StoreTeamMemberForm(forms.ModelForm):
    """Add a person to the store's own team, by their exact email.

    Staff identities are shared across every store (they live in the
    public schema), so a chooser of "all accounts" would show a store's
    Admin every other store's people and the platform's operators. The
    person has to be named instead: the address must match one existing
    platform account exactly. Nothing is listed, searched by prefix, or
    suggested, and the store is never a field: it is always this one.
    """

    email = forms.EmailField(
        label=_("Email address"),
        help_text=_(
            "The exact email address of the person's existing platform account."
        ),
    )

    class Meta:
        model = UserTenantMembership
        fields = ("role", "is_active")

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get("email")
        store = bound_tenant()
        if not email or store is None:
            return cleaned

        from user.models.account import UserAccount

        with _public_schema_context():
            matches = list(UserAccount.objects.filter(email__iexact=email)[:2])
            member = matches[0] if len(matches) == 1 else None
            already = (
                member is not None
                and UserTenantMembership.objects.filter(
                    user=member, tenant=store
                ).exists()
            )
        if member is None:
            self.add_error(
                "email",
                ValidationError(
                    _("No platform account uses this email address."),
                    code="unknown_email",
                ),
            )
        elif already:
            self.add_error(
                "email",
                ValidationError(
                    _("This person is already on the team."),
                    code="already_member",
                ),
            )
        else:
            self.instance.user = member
            self.instance.tenant = store
        return cleaned


@admin.register(UserTenantMembership)
class UserTenantMembershipAdmin(BaseModelAdmin):
    list_display = ["member", "tenant", "role", "is_active", "created_at"]
    list_filter = ["role", "is_active", "tenant"]
    search_fields = ["user__email", "user__username", "tenant__name"]
    autocomplete_fields = ["user", "tenant"]
    readonly_fields = ["created_at", "updated_at"]

    @admin.display(description=_("Member"))
    def member(self, obj):
        return obj.member

    def get_changelist(self, request, **kwargs):
        """A page of members is read from the public schema in one query."""
        changelist: Any = super().get_changelist(request, **kwargs)

        class MemberChangeList(changelist):
            def get_results(self, request):
                super().get_results(request)
                UserTenantMembership.load_members(self.result_list)

        return MemberChangeList

    def get_list_display(self, request):
        """A store's own team is all one store: no Store column."""
        columns = super().get_list_display(request)
        if self_service_tenant(request) is None:
            return columns
        return [column for column in columns if column != "tenant"]

    def get_list_filter(self, request):
        """The Store filter lists every store on the platform."""
        filters = super().get_list_filter(request)
        if self_service_tenant(request) is None:
            return filters
        return [entry for entry in filters if entry[0] != "tenant"]

    def get_search_fields(self, request):
        """Search joins the user table, which a store host resolves to
        that store's shoppers and not to the team's staff identities."""
        if self_service_tenant(request) is not None:
            return []
        return super().get_search_fields(request)

    def get_fields(self, request, obj=None):
        """A store role adds a person by email and never picks a store."""
        if self_service_tenant(request) is None:
            return super().get_fields(request, obj)
        if obj is None:
            return ["email", "role", "is_active"]
        return ["member", "role", "is_active"]

    def get_readonly_fields(self, request, obj=None):
        readonly = super().get_readonly_fields(request, obj)
        if self_service_tenant(request) is not None and obj is not None:
            return (*readonly, "member")
        return readonly

    def get_form(self, request, obj=None, change=False, **kwargs):
        if self_service_tenant(request) is not None and obj is None:
            kwargs["form"] = StoreTeamMemberForm
        return super().get_form(request, obj, change=change, **kwargs)

    def get_actions(self, request, *args, **kwargs):
        """Bulk delete keeps the store's last owner (a message, not a 500)."""
        actions: dict[str, Any] = super().get_actions(request, *args, **kwargs)
        if "delete_selected" in actions:
            _func, name, description = actions["delete_selected"]
            actions["delete_selected"] = (
                _delete_selected_keeping_an_owner,
                name,
                description,
            )
        return actions

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
        if obj is not None and obj.is_last_active_owner:
            return False
        return self._may_act_on(request, obj)

    def _may_act_on(self, request, obj) -> bool:
        scope = self_service_tenant(request)
        if scope is None or obj is None:
            return True
        if obj.role != TenantMembershipRole.OWNER:
            return True

        from tenant.membership import get_membership

        membership = get_membership(request.user, scope)
        return (
            membership is not None
            and membership.role == TenantMembershipRole.OWNER
        )

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        """The role is chosen, and an ADMIN cannot mint an OWNER."""
        if db_field.name == "role":
            scope = self_service_tenant(request)
            is_owner = False
            if scope is not None:
                from tenant.membership import get_membership

                membership = get_membership(request.user, scope)
                is_owner = (
                    membership is not None
                    and membership.role == TenantMembershipRole.OWNER
                )
            kwargs["choices"] = selectable_roles(
                may_grant_owner=scope is None or is_owner
            )
        return super().formfield_for_choice_field(db_field, request, **kwargs)


@wraps(delete_selected)
def _delete_selected_keeping_an_owner(modeladmin, request, queryset):
    """Django's bulk delete, answering a refusal with a message.

    ``has_delete_permission`` covers one row at a time; selecting every
    owner of a store at once is only caught when the delete runs.
    """
    try:
        return delete_selected(modeladmin, request, queryset)
    except ValidationError as exc:
        modeladmin.message_user(request, " ".join(exc.messages), messages.ERROR)
        return None


@admin.register(TenantArchive)
class TenantArchiveAdmin(BaseModelAdmin):
    """Read-only erasure records for destroyed tenants.

    GDPR art. 5(2) requires the controller to be able to DEMONSTRATE
    compliance, which a row nobody can see does not achieve. Every field
    is read-only and nothing can be added or deleted here: this is the
    evidence that a store was erased, what was kept, on what basis and
    until when — editing it would defeat the point, and deleting it
    would destroy the proof while leaving the retained files behind.
    """

    list_display = [
        "schema_name",
        "tenant_name",
        "destroyed_at",
        "display_retention",
        "data_exported",
    ]
    date_hierarchy = "created_at"
    list_filter = ["data_exported", "destroyed_at"]
    search_fields = ["schema_name", "tenant_name", "destroyed_by"]
    ordering = ["-destroyed_at"]

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description=_("Retention"))
    def display_retention(self, obj):
        if obj.purged_at is not None:
            return label(_("Purged"), "success", "delete_sweep")
        if obj.retention_until is None:
            return label(_("Nothing retained"), "info", "block")
        tone = "danger" if obj.retention_expired else "warning"
        return label(
            _("Until %(date)s") % {"date": obj.retention_until},
            tone,
            "gavel",
        )
