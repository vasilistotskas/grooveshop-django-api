"""Reusable admin mixins."""

from __future__ import annotations


class AdminSiteLoginNextMixin:
    """Land a successful admin login back in the admin.

    Django's ``LoginView.get_success_url()`` uses the ``next`` parameter
    and falls back to ``settings.LOGIN_REDIRECT_URL``, which is the
    STOREFRONT account page — correct for shoppers, wrong for staff.
    ``AdminSite.login`` does put ``next`` in the template context, but
    Unfold's ``admin/login.html`` renders ``<form action="{{ app_path }}">``
    and never emits it as a hidden field, so a POST from
    ``/admin/login`` carries no ``next`` at all.

    Reaching ``/admin/`` first is fine — that redirect appends
    ``?next=/admin/``, which rides along in ``app_path``. Opening
    ``/admin/login`` directly is what broke: production sent staff to
    ``https://webside.gr/account`` on 2026-08-21.

    Unfold documents ``UNFOLD["LOGIN"]["redirect_after"]`` for this, but
    0.104.1 (the current release) only DECLARES the key — the single
    occurrence in the package is its ``None`` default, and nothing reads
    it. So put ``next`` on the URL instead, which every layer below
    already understands.

    Lives in a mixin because EVERY admin site needs it: the tenant admin
    and the platform control plane both send staff to the storefront
    without it, and a fix applied to only one of them silently rots.
    ``reverse(..., current_app=self.name)`` keeps each site pointed at
    its own index.
    """

    def login(self, request, extra_context=None):
        from django.contrib.auth import REDIRECT_FIELD_NAME
        from django.shortcuts import redirect
        from django.urls import reverse

        if (
            request.method == "GET"
            and REDIRECT_FIELD_NAME not in request.GET
            and REDIRECT_FIELD_NAME not in request.POST
        ):
            index = reverse("admin:index", current_app=self.name)
            return redirect(f"{request.path}?{REDIRECT_FIELD_NAME}={index}")
        return super().login(request, extra_context)


# ``tenant`` is SHARED-only but is NOT withheld from a store's admin.
# ``TenantAdmin.get_queryset`` scopes a store operator to their own row
# and ``get_readonly_fields`` allowlists
# ``role_scopes.TENANT_SELF_EDITABLE_FIELDS`` — branding, socials, pixels,
# carrier credentials. That is the merchant self-service surface, so
# hiding the app would remove a feature rather than a leak.
STORE_SELF_SERVICE_APP_LABELS: frozenset[str] = frozenset({"tenant"})


class IsSuperuserOnlyModelAdmin:
    """Hide a ModelAdmin entirely from non-superusers.

    The sidebar/permission callback approach hides the menu entry, but a
    staff user could still reach the changelist by typing the URL. This
    mixin gates every admin permission method so the model becomes
    invisible and unreachable for anyone who isn't `is_superuser=True`.
    """

    def has_module_permission(self, request) -> bool:
        return bool(request.user.is_authenticated and request.user.is_superuser)

    def has_view_permission(self, request, obj=None) -> bool:
        return bool(request.user.is_authenticated and request.user.is_superuser)

    def has_add_permission(self, request) -> bool:
        return bool(request.user.is_authenticated and request.user.is_superuser)

    def has_change_permission(self, request, obj=None) -> bool:
        return bool(request.user.is_authenticated and request.user.is_superuser)

    def has_delete_permission(self, request, obj=None) -> bool:
        return bool(request.user.is_authenticated and request.user.is_superuser)


class WithheldOnTenantHostModelAdmin:
    """Withhold a PLATFORM-owned model while serving a tenant host.

    The mirror of ``BaseModelAdmin._withheld_on_public``. That one keeps
    tenant-only models off the control plane; this keeps control-plane
    models off a store's admin. Same failure shape, opposite direction:
    ``django_site`` lives only in ``public``, so opening it on
    ``api.<store>.gr/admin`` lists every store's domain — five rows, one
    per tenant — dressed as that merchant's own page. Reported from
    production 2026-09-12. ``core_cachepurgelog`` is worse: 146 rows
    recording every purge across the estate, actor column included.

    Gated on ``request.tenant`` and it withholds only when it POSITIVELY
    knows a real tenant is being served — the same positive-knowledge
    rule ``_withheld_on_public`` had to learn. Keying on
    ``connection.schema_name`` instead would fire during tests,
    management commands and Celery work, where no tenant is bound, and
    deny valid changelists.

    ``has_module_permission`` returning False is what removes the model
    from the app list, so one mechanism closes the navigation AND the
    typed URL; the remaining methods make the direct URL a clean 403
    rather than a page of another store's data.

    Only a platform superuser ever reached these anyway — no store role
    is granted a permission on a ``PLATFORM_ONLY_APP_LABELS`` app — so
    this is about an operator landing on the wrong console, not about a
    merchant seeing data they could otherwise reach.
    """

    def _withheld_on_tenant_host(self, request) -> bool:
        from django_tenants.utils import get_public_schema_name

        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return False
        schema = getattr(tenant, "schema_name", None)
        if schema is None or schema == get_public_schema_name():
            return False

        from tenant.app_labels import shared_only_app_labels

        if self.model._meta.app_label in STORE_SELF_SERVICE_APP_LABELS:
            return False
        return self.model._meta.app_label in set(shared_only_app_labels())

    def has_module_permission(self, request) -> bool:
        if self._withheld_on_tenant_host(request):
            return False
        return super().has_module_permission(request)

    def has_view_permission(self, request, obj=None) -> bool:
        if self._withheld_on_tenant_host(request):
            return False
        return super().has_view_permission(request, obj)

    def has_add_permission(self, request) -> bool:
        if self._withheld_on_tenant_host(request):
            return False
        return super().has_add_permission(request)

    def has_change_permission(self, request, obj=None) -> bool:
        if self._withheld_on_tenant_host(request):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None) -> bool:
        if self._withheld_on_tenant_host(request):
            return False
        return super().has_delete_permission(request, obj)
