"""Reusable admin mixins."""

from __future__ import annotations

from django.forms.models import _get_foreign_key

from admin.features import (
    PLACEMENTS,
    Gate,
    Kind,
    Placement,
    store_features,
)


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
    0.109.0 (re-checked 2026-10-13) still only DECLARES the key — the single
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


class AdminFooterMixin:
    """The footer every admin site renders (``admin/footer.html``).

    The running release (support starts with "which version?"), the
    help site, and the API docs for platform superusers - the only
    people who read them. Lives in a mixin for the same reason as the
    login fix above: both sites render it.
    """

    #: The URL name of the API docs on this site's URLconf, if it has any.
    api_docs_url_name: str | None = None

    def each_context(self, request):
        from django.conf import settings
        from django.urls import reverse
        from django.utils.translation import gettext as _

        from tenant.membership import is_platform_superuser

        context = super().each_context(request)
        links = []
        if settings.ADMIN_DOCS_URL:
            links.append(
                {"title": _("Help & Guides"), "link": settings.ADMIN_DOCS_URL}
            )
        if self.api_docs_url_name and is_platform_superuser(request.user):
            links.append(
                {
                    "title": _("API docs"),
                    "link": reverse(self.api_docs_url_name),
                }
            )
        context["admin_footer"] = {
            "version": settings.RELEASE_VERSION,
            "links": links,
        }
        return context


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

    Each check runs the bases' own guard FIRST (``BaseModelAdmin``'s
    tenant-host withholding, a read-only admin's refusals) and then
    requires a superuser, so both apply and a withheld model answers
    before the user is even looked at.
    """

    @staticmethod
    def _is_superuser(request) -> bool:
        return bool(request.user.is_authenticated and request.user.is_superuser)

    def has_module_permission(self, request) -> bool:
        return super().has_module_permission(request) and self._is_superuser(
            request
        )

    def has_view_permission(self, request, obj=None) -> bool:
        return super().has_view_permission(request, obj) and self._is_superuser(
            request
        )

    def has_add_permission(self, request) -> bool:
        return super().has_add_permission(request) and self._is_superuser(
            request
        )

    def has_change_permission(self, request, obj=None) -> bool:
        return super().has_change_permission(
            request, obj
        ) and self._is_superuser(request)

    def has_delete_permission(self, request, obj=None) -> bool:
        return super().has_delete_permission(
            request, obj
        ) and self._is_superuser(request)


class WithheldOnTenantHostModelAdmin:
    """Withhold a PLATFORM-owned model while serving a tenant host.

    The store admin registers every model, including the control plane's
    own (the platform site copies only those — ``register_platform_models``).
    ``django_site`` lives only in ``public``, so opening it on
    ``api.<store>.gr/admin`` lists every store's domain — five rows, one
    per tenant — dressed as that merchant's own page. Reported from
    production 2026-09-12. ``core_cachepurgelog`` is worse: 146 rows
    recording every purge across the estate, actor column included.
    (The Cache page's embedded "recent activity" panel is separately
    scoped by ``CachePurgeLog.objects.visible_here()``; this changelist
    is NOT — it is withheld instead, which is why both exist.)

    Gated on ``request.tenant`` and it withholds only when it POSITIVELY
    knows a real tenant is being served. Keying on
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
        # Reference data stays READABLE: a store's own forms pick a
        # country or region from it (the autocomplete endpoint checks
        # the target admin's view permission). Everything else of the
        # platform's is refused outright.
        from tenant.role_scopes import REFERENCE_DATA_APP_LABELS

        if (
            self._withheld_on_tenant_host(request)
            and self.model._meta.app_label not in REFERENCE_DATA_APP_LABELS
        ):
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


def _without_fields(fields, hidden: set[str]) -> list:
    """``fields`` of a fieldset minus ``hidden``; a row (a nested tuple)
    that loses all its fields goes too."""
    kept = []
    for entry in fields:
        if isinstance(entry, (list, tuple)):
            if row := _without_fields(entry, hidden):
                kept.append(tuple(row))
        elif entry not in hidden:
            kept.append(entry)
    return kept


class FeatureGatedModelAdmin:
    """Show a model's pages, and the feature surfaces on them, by the one
    rule of ``admin.features``.

    The model's own page: ``FEATURE_SURFACES`` names the feature that
    owns it and whether it is setup or records. ``has_module_permission``
    and the four object permissions all answer from that, so the sidebar,
    the ⌘K palette and the dashboard (built from ``has_module_permission``)
    omit a page the store cannot use and holds nothing for, and a typed
    URL to it is refused rather than merely not linked. Off the plan, what
    a store still holds is read-only; a record is only added while its
    feature is live.

    Fields and list filters of other features on this model's admin are
    declared in ``feature_gates``.
    """

    feature_gates: tuple[Gate, ...] = ()

    def _page_placement(self, request) -> Placement | None:
        if request is None:
            return None
        return PLACEMENTS.get(self.model._meta.label)

    def _page_shown(self, request, placement: Placement) -> bool:
        features = store_features(request)
        label = self.model._meta.label
        return features.shows(
            request,
            placement.feature,
            kind=placement.kind,
            has_data=lambda: features.has_rows(label),
        )

    def has_module_permission(self, request) -> bool:
        placement = self._page_placement(request)
        if placement is not None and not self._page_shown(request, placement):
            return False
        return super().has_module_permission(request)

    def has_view_permission(self, request, obj=None) -> bool:
        placement = self._page_placement(request)
        if placement is not None and not self._page_shown(request, placement):
            return False
        return super().has_view_permission(request, obj)

    def has_add_permission(self, request) -> bool:
        placement = self._page_placement(request)
        if placement is not None and not (
            self._page_shown(request, placement)
            and store_features(request).may_create(
                placement.feature, placement.kind
            )
        ):
            return False
        return super().has_add_permission(request)

    def has_change_permission(self, request, obj=None) -> bool:
        placement = self._page_placement(request)
        if placement is not None and not (
            self._page_shown(request, placement)
            and store_features(request).may_modify(placement.feature)
        ):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None) -> bool:
        placement = self._page_placement(request)
        if placement is not None and not (
            self._page_shown(request, placement)
            and store_features(request).may_modify(placement.feature)
        ):
            return False
        return super().has_delete_permission(request, obj)

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if not self.feature_gates:
            return fieldsets
        features = store_features(request)
        hidden = {
            name
            for gate in self.feature_gates
            if not features.gate_shows(request, self.model, gate, obj)
            for name in gate.fields
        }
        if not hidden:
            return fieldsets
        kept = []
        for title, options in fieldsets:
            fields = _without_fields(options["fields"], hidden)
            if fields or not set(options["fields"]) & hidden:
                kept.append((title, {**options, "fields": tuple(fields)}))
        return kept

    def get_list_filter(self, request):
        entries = super().get_list_filter(request)
        if not self.feature_gates:
            return entries
        features = store_features(request)
        hidden = [
            entry
            for gate in self.feature_gates
            if not features.gate_shows(request, self.model, gate)
            for entry in gate.filters
        ]
        return [entry for entry in entries if entry not in hidden]


class FeatureInline:
    """An inline that belongs to a feature (``feature``): shown while the
    feature is live, or when the parent object has rows of it. With the
    feature off the rows are there to read: no row is added, and off the
    plan none is changed either. Django drops an inline with no
    permission at all, so its tab goes without touching ``get_inlines``.
    """

    feature: str

    def _inline_shown(self, request, obj) -> bool:
        features = store_features(request)

        def has_rows() -> bool:
            if obj is None or obj.pk is None:
                return False
            fk = _get_foreign_key(
                self.parent_model, self.model, fk_name=self.fk_name
            )
            return self.model._default_manager.filter(**{fk.name: obj}).exists()

        key = ("inline", type(self), getattr(obj, "pk", None))
        return features.remember(
            key,
            lambda: features.shows(
                request, self.feature, kind=Kind.RECORD, has_data=has_rows
            ),
        )

    def has_view_permission(self, request, obj=None) -> bool:
        return self._inline_shown(request, obj) and super().has_view_permission(
            request, obj
        )

    def has_add_permission(self, request, obj=None) -> bool:
        return (
            self._inline_shown(request, obj)
            and store_features(request).may_create(self.feature, Kind.RECORD)
            and super().has_add_permission(request, obj)
        )

    def has_change_permission(self, request, obj=None) -> bool:
        return (
            self._inline_shown(request, obj)
            and store_features(request).may_modify(self.feature)
            and super().has_change_permission(request, obj)
        )

    def has_delete_permission(self, request, obj=None) -> bool:
        return (
            self._inline_shown(request, obj)
            and store_features(request).may_modify(self.feature)
            and super().has_delete_permission(request, obj)
        )
