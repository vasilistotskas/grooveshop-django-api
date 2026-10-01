from __future__ import annotations

import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import path
from django.utils.translation import gettext_lazy as _
from unfold.sites import UnfoldAdminSite

from admin.displays import format_dt, label_cell
from admin.forms import CachePurgeForm, PlatformAdminAuthenticationForm
from admin.mixins import AdminSiteLoginNextMixin
from core.cache import CacheService
from core.cache.nuxt import is_configured as nuxt_purge_configured
from core.cache.registry import iter_surfaces

logger = logging.getLogger(__name__)


def _require_purge_permission(request) -> None:
    """The cache pages are behind ``core.purge_cache`` (store ADMIN and
    OWNER, and platform superusers), not merely the admin login."""
    if not request.user.has_perm("core.purge_cache"):
        raise PermissionDenied


def _purge_log_table(logs) -> dict:
    """Unfold's table component for the recent purges.

    ``actor_email``, never the ``actor`` FK: the log lives only in the
    public schema, so resolving the FK from a tenant host names whoever
    shares that id there.
    """
    return {
        "headers": [
            _("When"),
            _("Actor"),
            _("Surfaces"),
            _("Django"),
            _("Nuxt"),
            _("Blocked"),
            _("Mode"),
        ],
        "rows": [
            [
                format_dt(log.created_at),
                log.actor_email or "—",
                ", ".join(log.surfaces or []),
                log.total_django,
                log.total_nuxt,
                log.total_blocked,
                label_cell(_("Dry run"), "info")
                if log.dry_run
                else label_cell(_("Live"), "success"),
            ]
            for log in logs
        ],
    }


class MyAdminSite(AdminSiteLoginNextMixin, UnfoldAdminSite):
    index_title = _("Dashboard")

    # Admin sessions are platform-staff-only — no legacy tenant-schema
    # login path. UnfoldAdminSite.__init__ only overrides this when it
    # is still None (or when UNFOLD["LOGIN"]["form"] is configured, which
    # it isn't here), so setting it as a class attribute wins.
    login_form = PlatformAdminAuthenticationForm

    def has_permission(self, request) -> bool:
        """Membership-gate the tenant admin.

        ``is_staff`` is a GLOBAL flag on the shared ``UserAccount`` —
        without this override, staff granted for one store could open
        every other store's ``/admin/``. Rules:

        - The session MUST have been authenticated via
          ``PlatformStaffBackend`` (checked first — closes a
          pk-collision ambiguity where a tenant-schema user's pk could
          coincidentally match a public-schema membership's user_id).
        - Platform superusers pass everywhere (they manage all tenants;
          they are platform identities too, authenticated by the same
          backend).
        - With no store bound (the single-schema test lane, management
          commands): Django's default ``is_active and is_staff``. The
          store admin is never mounted on the public host.
        - On a TENANT schema: additionally require an active
          ``UserTenantMembership`` with a staff-capable role
          (STAFF/ADMIN/OWNER) in THIS tenant.
        """
        if not super().has_permission(request):
            return False

        from tenant.auth_backends import (
            is_platform_staff_session,
        )

        if not is_platform_staff_session(request):
            return False

        user = request.user
        if user.is_superuser:
            return True

        from tenant.membership import (
            get_current_tenant,
            get_membership,
        )

        tenant = get_current_tenant()
        if tenant is None:
            return True
        membership = get_membership(user, tenant)
        return membership is not None and membership.is_tenant_staff

    def password_change(self, request, extra_context=None):
        """Run the admin's own password-change view against the PUBLIC schema.

        ``request.user`` for a platform-staff session is always the
        public-schema row (see ``PlatformStaffBackend``), but the view
        runs under whatever schema the current HOST resolved to — on a
        tenant host that's the tenant schema, where the staff user's
        row does not exist. ``PasswordChangeForm.save()`` would try to
        UPDATE the wrong (or a missing) row.
        """
        from tenant.auth_backends import (
            is_platform_staff_session,
        )

        if is_platform_staff_session(request):
            from django_tenants.utils import (
                get_public_schema_name,
                schema_context,
            )

            with schema_context(get_public_schema_name()):
                return super().password_change(request, extra_context)
        return super().password_change(request, extra_context)

    def each_context(self, request):
        """Title the admin with the store being served.

        ``UNFOLD["SITE_HEADER"]``/``["SITE_TITLE"]`` are the deployment's
        identity; on a store's own host the operator must always see
        WHICH store they are editing, so its name replaces them.
        """
        context = super().each_context(request)
        from tenant.membership import get_current_tenant

        tenant = get_current_tenant()
        if tenant is not None:
            name = tenant.store_name or tenant.name
            context["site_header"] = name
            context["site_title"] = _("%(name)s Admin") % {"name": name}
        return context

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "clear-cache/",
                self.admin_view(self.clear_cache_view),
                name="clear-cache",
            ),
        ]
        return custom_urls + urls

    def clear_cache_view(self, request):
        _require_purge_permission(request)
        if request.method == "POST":
            return self._handle_purge(request)

        from core.cache.models import CachePurgeLog

        surfaces = iter_surfaces()
        try:
            counts = CacheService.count(s.code for s in surfaces)
        except Exception:
            # ``keys()`` now raises instead of reporting an empty scan,
            # so a Redis outage reaches here. Showing every surface at
            # zero would read as "nothing to purge".
            # The exception text stays in the log, not in the page. A
            # redis-py connection error carries the connection target,
            # and the URL in this deployment carries the password.
            logger.exception("Cache surface counts unavailable")
            messages.error(
                request,
                _(
                    "Live key counts are unavailable — the cache backend"
                    " did not answer. The figures below are not reliable;"
                    " see the application log for the error."
                ),
            )
            counts = {}
        form = CachePurgeForm(surfaces=surfaces)
        checkboxes = {
            option.data["value"]: option for option in form["surfaces"]
        }
        groups: dict[str, list] = {}
        for surface in surfaces:
            groups.setdefault(surface.group, []).append(
                {
                    "checkbox": checkboxes[surface.code],
                    "code": surface.code,
                    "label": surface.label,
                    "description": surface.description,
                    "icon": surface.icon,
                    "danger": surface.danger,
                    "count": counts.get(surface.code, 0),
                    "related": surface.related,
                    "django_patterns": surface.django_patterns,
                    "nuxt_patterns": surface.nuxt_patterns,
                }
            )
        # Scoped to the store doing the reading. The table lives only in
        # ``public``, so an unscoped query on a tenant host falls through and
        # shows every store's activity — and, because ``actor`` is a
        # cross-schema FK, credits it to whoever shares that id in the reading
        # schema. Rows written before ``schema_name`` existed carry no owner,
        # so they stay on the control plane rather than being shown to a store
        # that may not have caused them. ``actor_email`` is read instead of the
        # FK for the same reason.
        recent_logs = CachePurgeLog.objects.visible_here()[:20]
        context = {
            **self.each_context(request),
            "form": form,
            "groups": sorted(groups.items()),
            "recent_logs": _purge_log_table(recent_logs),
            "nuxt_configured": nuxt_purge_configured(),
            "title": _("Cache Management"),
        }
        return render(request, "admin/clear_cache.html", context)

    def _handle_purge(self, request):
        form = CachePurgeForm(request.POST, surfaces=iter_surfaces())
        if not form.is_valid():
            messages.error(request, _("Unknown cache surface."))
            return redirect("admin:clear-cache")
        codes = form.cleaned_data["surfaces"]
        include_related = form.cleaned_data["include_related"]
        action = request.POST.get("action", "purge")
        dry_run = action == "dry_run"

        if action == "purge_all":
            report = CacheService.purge_all(dry_run=False, actor=request.user)
        elif not codes:
            messages.warning(
                request,
                _("Select at least one cache surface to purge."),
            )
            return redirect("admin:clear-cache")
        else:
            report = CacheService.purge(
                codes,
                dry_run=dry_run,
                actor=request.user,
                include_related=include_related,
            )

        # ``*_headline``, not ``total_*``: a dry run deletes nothing, so
        # the deleted totals are always 0 and every dry run reported
        # "0 keys would be removed".
        summary = {
            "d": report.django_headline,
            "n": report.nuxt_headline,
            "s": len(report.surfaces),
        }
        if dry_run:
            messages.info(
                request,
                _(
                    "Dry run: %(d)s Django + %(n)s Nuxt keys would be"
                    " removed across %(s)s surface(s)."
                )
                % summary,
            )
        else:
            messages.success(
                request,
                _(
                    "Purged %(d)s Django + %(n)s Nuxt keys"
                    " across %(s)s surface(s)."
                )
                % summary,
            )

        # Django-side failures were never surfaced here at all, so a
        # Redis outage rendered as a green "Purged 0 Django keys".
        django_failures = [s for s in report.surfaces if s.django_error]
        if django_failures:
            messages.error(
                request,
                _(
                    "Django cache purge FAILED for %(n)s surface(s):"
                    " %(codes)s. Those keys are still cached — see the"
                    " application log for the backend error."
                )
                % {
                    "n": len(django_failures),
                    "codes": ", ".join(s.code for s in django_failures),
                },
            )
        nuxt_failures = [s for s in report.surfaces if s.nuxt_error]
        if nuxt_failures:
            messages.warning(
                request,
                _(
                    "Nuxt purge unreachable for %(n)s surface(s)."
                    " Check NUXT_INTERNAL_BASE_URL +"
                    " NUXT_CACHE_PURGE_TOKEN."
                )
                % {"n": len(nuxt_failures)},
            )
        return redirect("admin:clear-cache")
