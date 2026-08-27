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

from datetime import timedelta
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.db import connection, transaction
from django.forms.models import ModelChoiceIterator
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import ChoicesRadioFilter
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from tenant.lifecycle import (
    PROTECTED_SCHEMAS,
    activate_tenant,
    suspend_tenant,
)
from tenant.models import (
    SuspendedReason,
    Tenant,
    TenantArchive,
    TenantDomain,
    TenantMembershipRole,
    UserTenantMembership,
)

# Schemas that can never be suspended, activated, or destroyed via the
# admin. Destroying these would break the platform. Aliased from
# tenant.lifecycle (the shared source of truth) so the admin's guards
# and the billing task's can never disagree.
_PROTECTED = PROTECTED_SCHEMAS

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


# Plan presentation: an icon and a colour per tier, so the estate reads
# at a glance instead of as a column of lowercase words.
#
# Rendered through Unfold's own ``label.html`` rather than hand-written
# markup: that template already accepts ``icon`` (``@display(label=...)``
# does NOT — the decorator only carries a value->colour map, verified
# against unfold 0.104.1), and reusing it means the badges inherit
# Unfold's palette and dark-mode classes instead of duplicating them
# here where they would silently drift on upgrade.
_PLAN_BADGES: dict[str, tuple[str, str]] = {
    "trial": ("schedule", "warning"),
    "basic": ("storefront", "info"),
    "pro": ("rocket_launch", "primary"),
    "enterprise": ("workspace_premium", "success"),
}

# Billing state presentation: (label, unfold label tone, material icon),
# keyed by ``tenant.billing.billing_state()``'s return value. Canonical
# home for this map — the Plan & Billing page (admin/platform_billing.py)
# imports it from here (mirroring how it already borrows ``_PLAN_BADGES``)
# so the two surfaces cannot drift as states change.
_STATE_BADGES: dict[str, tuple[Any, str, str]] = {
    "suspended": (_("Suspended"), "danger", "pause_circle"),
    "past_due": (_("Past due"), "danger", "event_busy"),
    "expiring": (_("Expires soon"), "warning", "hourglass_top"),
    "trial": (_("Trial"), "warning", "schedule"),
    "unbilled": (_("No term recorded"), "info", "contract"),
    "paid": (_("Paid"), "success", "check_circle"),
}


def _unfold_label(text, tone: str, icon: str | None = None) -> str:
    from django.template.loader import render_to_string  # noqa: PLC0415

    return render_to_string(
        "unfold/helpers/label.html",
        {"text": text, "type": tone, "icon": icon},
    )


def _public_schema_context():
    from django_tenants.utils import (  # noqa: PLC0415
        get_public_schema_name,
        schema_context,
    )

    return schema_context(get_public_schema_name())


class _PublicSchemaModelChoiceIterator(ModelChoiceIterator):
    """Iterate the field's queryset inside the PUBLIC schema.

    The generator holds the schema context open across yields; admin
    rendering consumes every choice, so the context is exited when the
    iterator is exhausted.
    """

    def __iter__(self):
        with _public_schema_context():
            yield from super().__iter__()


class PublicSchemaModelChoiceField(forms.ModelChoiceField):
    """A ``ModelChoiceField`` pinned to the PUBLIC schema for BOTH
    rendering and validation.

    ``UserTenantMembership`` lives in the SHARED ``tenant`` app and its
    ``user`` FK targets ``public.user_useraccount``. Served on a tenant
    host (search_path = [tenant, public]), a plain ``ModelChoiceField``
    re-evaluates its queryset against that tenant's OWN
    ``user_useraccount`` copy — listing that store's shoppers, and
    (worse) binding a submitted pk to whichever PUBLIC identity happens
    to share it: a different person than the one displayed. Every DB
    access here is wrapped in ``schema_context(public)`` so the choices
    and the validated object are always the platform identities the FK
    can legitimately reference.
    """

    iterator = _PublicSchemaModelChoiceIterator

    def to_python(self, value):
        with _public_schema_context():
            return super().to_python(value)

    def valid_value(self, value):
        with _public_schema_context():
            return super().valid_value(value)


@admin.register(Tenant)
class TenantAdmin(ModelAdmin):
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
    readonly_fields = [
        "schema_name",
        "uuid",
        "created_at",
        "updated_at",
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
        way to identify a row.
        """
        primary = obj.domains.filter(is_primary=True).first()
        name = obj.store_name or obj.name
        avatar = (
            {"path": obj.logo_light_url, "squared": True}
            if obj.logo_light_url
            else None
        )
        return [
            name,
            primary.domain if primary else obj.schema_name,
            "".join(word[:1] for word in name.split()[:2]).upper(),
            avatar,
        ]

    @display(description=_("Plan"), ordering="plan")
    def display_plan(self, obj):
        icon, tone = _PLAN_BADGES.get(obj.plan, ("help", "info"))
        return mark_safe(  # noqa: S308 - fixed strings, no user input
            _unfold_label(obj.get_plan_display(), tone, icon)
        )

    @display(description=_("Status"), ordering="is_active")
    def display_status(self, obj):
        """Suspended is distinct from inactive — different operations.

        A suspended store is mid-lifecycle (24h cooldown before it can
        be destroyed); an inactive one was simply switched off. Showing
        both as a bare boolean hid that difference.
        """
        if obj.suspended_at is not None:
            return mark_safe(  # noqa: S308 - fixed strings
                _unfold_label(_("Suspended"), "danger", "pause_circle")
            )
        if not obj.is_active:
            return mark_safe(  # noqa: S308 - fixed strings
                _unfold_label(_("Inactive"), "warning", "visibility_off")
            )
        return mark_safe(  # noqa: S308 - fixed strings
            _unfold_label(_("Live"), "success", "check_circle")
        )

    @display(description=_("Billing"))
    def display_billing_state(self, obj):
        """The same classifier the Plan & Billing page renders (see
        ``tenant.billing.billing_state``) — surfaced here too so a
        past-due store is visible without leaving the Tenants list."""
        from tenant.billing import billing_state  # noqa: PLC0415

        state = billing_state(obj, timezone.localdate())
        label, tone, icon = _STATE_BADGES[state]
        return mark_safe(  # noqa: S308 - fixed strings, no user input
            _unfold_label(str(label), tone, icon)
        )

    @display(description=_("Last activity"))
    def display_last_activity(self, obj):
        """Latest order in the tenant's OWN schema.

        Same ``tenant_context`` + schema-existence guard as the
        platform dashboard's estate table
        (``admin/platform_dashboard.py::_tenant_rows``) — a
        half-provisioned or not-yet-migrated schema must read as
        "cannot tell" ("—"), not a misleading blank/zero. The
        platform's own row is skipped outright: ``order`` is a
        TENANT_APPS-only app, so the public schema has no orders table
        to query at all.
        """
        from django_tenants.utils import get_public_schema_name  # noqa: PLC0415

        from admin.platform_dashboard import _schema_exists  # noqa: PLC0415

        if obj.schema_name == get_public_schema_name():
            return "—"
        if not _schema_exists(obj.schema_name):
            return "—"

        from django.apps import apps  # noqa: PLC0415
        from django_tenants.utils import tenant_context  # noqa: PLC0415

        from admin.displays import format_dt  # noqa: PLC0415

        try:
            with tenant_context(obj):
                Order = apps.get_model("order", "Order")
                latest = (
                    Order.objects.order_by("-created_at")
                    .values_list("created_at", flat=True)
                    .first()
                )
        except Exception:  # noqa: BLE001 - never break the changelist
            return "—"

        return format_dt(latest) if latest is not None else "—"

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
        if obj is not None and obj.schema_name in _PROTECTED:
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
        from tenant.provisioning import provision_tenant  # noqa: PLC0415

        try:
            tenant = Tenant.objects.get(pk=tenant_pk)
        except Tenant.DoesNotExist:  # pragma: no cover - defensive
            return

        try:
            result = provision_tenant(tenant)
        except Exception as exc:  # noqa: BLE001 - never break the add response
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
                    "suspended_reason",
                    "uuid",
                ]
            },
        ),
        (
            _("Plan & Billing"),
            {
                "fields": [
                    "plan",
                    "paid_until",
                    "billing_notice_stage",
                    "billing_notice_term",
                    "stripe_connect_account_id",
                ]
            },
        ),
        (
            _("Branding"),
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
            _("Theme"),
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
            _("Features"),
            {
                "fields": [
                    "loyalty_enabled",
                    "blog_enabled",
                    "promotions_enabled",
                    "gift_cards_enabled",
                    "agent_commerce_enabled",
                ]
            },
        ),
        (
            _("Analytics"),
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
            _("Social Links"),
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
            _("Email"),
            {
                "fields": [
                    "from_email",
                    "contact_email",
                ],
                "classes": ["collapse"],
            },
        ),
        (
            _("Authentication"),
            {
                "fields": ["totp_issuer"],
                "classes": ["collapse"],
            },
        ),
        (
            _("Agentic Commerce"),
            {
                "fields": [
                    "chat_api_key",
                    "acp_bearer_token",
                    "agent_hosted_payment_enabled",
                    "agent_stripe_delegated_enabled",
                ],
                "classes": ["collapse"],
            },
        ),
        (
            _("Security"),
            {
                "fields": ["allowed_csp_sources"],
                "classes": ["collapse"],
            },
        ),
        (
            _("Payments — Viva Wallet"),
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
            _("Shipping — ACS"),
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
            _("Shipping — BoxNow"),
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
            _("Payments — Stripe"),
            {
                "fields": [
                    "stripe_publishable_key",
                    "stripe_secret_key",
                ],
                "classes": ["collapse"],
            },
        ),
        (
            _("Timestamps"),
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
        description=str(_("Provision Stripe webhook")),
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
        from tenant.provisioning import provision_stripe  # noqa: PLC0415

        level_by_status = {
            "created": messages.SUCCESS,
            "exists": messages.INFO,
            "no_key": messages.WARNING,
            "no_domain": messages.ERROR,
        }

        for tenant in queryset:
            if tenant.schema_name in _PROTECTED:
                continue
            try:
                result = provision_stripe(tenant)
            except Exception as exc:  # noqa: BLE001 — surfaced to operator
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
        description=str(_("Export store data (before destroying)")),
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
        from tenant.lifecycle import export_tenant_data  # noqa: PLC0415

        for tenant in queryset:
            if tenant.schema_name in _PROTECTED:
                continue
            try:
                path = export_tenant_data(
                    tenant, actor=getattr(request.user, "email", "") or ""
                )
            except Exception as exc:  # noqa: BLE001 — surfaced to operator
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
        description=str(_("Suspend selected tenants")),
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
            if tenant.schema_name in _PROTECTED:
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
        description=str(_("Activate selected tenants")),
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
            if tenant.schema_name in _PROTECTED:
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
        description=str(_("Permanently destroy tenant + drop schema")),
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
        1. Schema is not in ``_PROTECTED`` (public / webside).
        2. Tenant is suspended (``is_active=False``).
        3. ``suspended_at`` is at least 24 hours in the past —
           prevents accidental destruction immediately after suspension.

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
                from tenant.lifecycle import destroy_tenant  # noqa: PLC0415

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

        The changelist/index URLs are resolved against
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
            "index_url": reverse(f"{site_name}:index"),
        }
        return render(
            request, "admin/tenant/destroy_confirmation.html", context
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

    def get_autocomplete_fields(self, request):
        """Drop the ``user`` autocomplete on tenant hosts.

        The autocomplete AJAX view runs its search in the REQUEST's
        schema, so on a tenant host it would surface that store's
        shoppers and let one be picked into a public-identity FK. There
        the field is rendered as a plain select through the public-
        pinned ``PublicSchemaModelChoiceField`` instead. ``tenant`` keeps
        autocomplete (Tenant lives only in public); the platform host
        (public schema) keeps ``user`` autocomplete, which resolves
        correctly there.
        """
        fields = super().get_autocomplete_fields(request)
        if self_service_tenant(request) is not None:
            return tuple(f for f in fields if f != "user")
        return fields

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
        """Pin the ``user`` FK to the PUBLIC schema.

        ``UserAccount`` is mirrored per-schema — an unqualified
        ``UserAccount.objects.all()`` resolves against whatever schema
        the connection is currently pinned to. Membership rows only
        ever reference PUBLIC-schema users (platform identities), so the
        candidate pks are captured eagerly inside
        ``schema_context(public)``. That alone is NOT enough: the
        queryset handed to the formfield is still lazy and, on a tenant
        host, would be re-evaluated against that tenant's own user
        table. On tenant hosts the field is therefore
        ``PublicSchemaModelChoiceField``, which pins rendering AND
        validation to public so a saved pk can only bind to the platform
        identity it displayed. On the platform host (public schema) the
        default field already resolves correctly.
        """
        if db_field.name == "user":
            from django.contrib.auth import get_user_model

            user_model = get_user_model()
            with _public_schema_context():
                public_user_ids = list(
                    user_model.objects.order_by(
                        user_model.USERNAME_FIELD
                    ).values_list("pk", flat=True)
                )
            kwargs["queryset"] = user_model.objects.filter(
                pk__in=public_user_ids
            )
            if self_service_tenant(request) is not None:
                kwargs["form_class"] = PublicSchemaModelChoiceField
        elif db_field.name == "tenant":
            # A store operator may only grant membership in THEIR store.
            # Without this the dropdown lists every tenant, and saving
            # one would hand them a foothold in another merchant's admin.
            scope = self_service_tenant(request)
            if scope is not None:
                kwargs["queryset"] = Tenant.objects.filter(pk=scope.pk)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(TenantArchive)
class TenantArchiveAdmin(ModelAdmin):
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
            return _unfold_label(_("Purged"), "success", "delete_sweep")
        if obj.retention_until is None:
            return _unfold_label(_("Nothing retained"), "info", "block")
        tone = "danger" if obj.retention_expired else "warning"
        return _unfold_label(
            _("Until %(date)s") % {"date": obj.retention_until},
            tone,
            "gavel",
        )
