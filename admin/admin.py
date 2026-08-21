from __future__ import annotations

from os import getenv

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import path
from django.utils.translation import gettext_lazy as _
from unfold.sites import UnfoldAdminSite

from admin.forms import PlatformAdminAuthenticationForm
from admin.mixins import AdminSiteLoginNextMixin
from core.cache import CacheService
from core.cache.nuxt import is_configured as nuxt_purge_configured
from core.cache.registry import iter_surfaces


# Platform console identity. Deliberately NOT the UNFOLD_SITE_HEADER
# defaults: those are tenant #1's ("Webside"), and the control plane must
# not wear a merchant's name.
PLATFORM_SITE_HEADER = "Grooveshop Platform"
PLATFORM_SITE_TITLE = _("Platform Admin")
PLATFORM_SITE_SUBHEADER = _("Control plane")


class MyAdminSite(AdminSiteLoginNextMixin, UnfoldAdminSite):
    site_header = getenv("UNFOLD_SITE_HEADER", "Webside")
    site_title = getenv("UNFOLD_SITE_TITLE", "Webside Admin")
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
        - On the PUBLIC schema (platform admin): Django's default
          ``is_active and is_staff``.
        - On a TENANT schema: additionally require an active
          ``UserTenantMembership`` with a staff-capable role
          (STAFF/ADMIN/OWNER) in THIS tenant.
        """
        if not super().has_permission(request):
            return False

        from tenant.auth_backends import (  # noqa: PLC0415
            is_platform_staff_session,
        )

        if not is_platform_staff_session(request):
            return False

        user = request.user
        if user.is_superuser:
            return True

        from tenant.membership import (  # noqa: PLC0415
            get_current_tenant,
            get_membership,
        )

        tenant = get_current_tenant()
        if tenant is None:
            # Public schema — the platform admin. Default rules apply.
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
        from tenant.auth_backends import (  # noqa: PLC0415
            is_platform_staff_session,
        )

        if is_platform_staff_session(request):
            from django_tenants.utils import (  # noqa: PLC0415
                get_public_schema_name,
                schema_context,
            )

            with schema_context(get_public_schema_name()):
                return super().password_change(request, extra_context)
        return super().password_change(request, extra_context)

    def get_app_list(self, request, app_label=None):
        """Hide tenant-only models while serving the PUBLIC schema.

        Their tables exist only inside tenant schemas, so opening one on
        the platform host is always an error — and not a clean 404: the
        pre-multi-tenant public schema still carries same-named legacy
        tables that newer migrations never touch (they are TENANT_APPS
        migrations), so Django queried ``public.order_order`` and got
        ``column order_order.loyalty_discount does not exist`` — a 500
        on the platform operator's own control plane. Pruning that
        legacy debris does not fix it either; the query would then fail
        on a missing relation instead.

        Public is the control plane: tenants, users, platform settings.
        Per-store data is edited on that store's own admin host.

        Scope note: this removes every in-app path to those pages, which
        is the failure mode that actually bites (an operator clicking a
        link on their own control plane). It deliberately does NOT deny
        the model permissions themselves — that was tried and is wrong:
        the check has to be "am I serving public", but outside a request
        there is no tenant on the connection at all, so the same test
        fires during tests, management commands and Celery work and
        denied 35 admin changelists that were perfectly valid. Typing a
        tenant-only admin URL directly on the platform host therefore
        still errors; it is an operator asking for a model that does not
        belong to that host.
        """
        app_list = super().get_app_list(request, app_label)
        from tenant.membership import get_current_tenant  # noqa: PLC0415

        if get_current_tenant() is not None:
            return app_list

        from tenant.app_labels import tenant_only_app_labels  # noqa: PLC0415

        hidden = set(tenant_only_app_labels())
        return [app for app in app_list if app.get("app_label") not in hidden]

    def each_context(self, request):
        """Brand the admin for whichever console is being served.

        Three cases, and the third is the one that bit us:

        - TENANT host: show that store's name, so an operator always
          knows which store they are editing.
        - PLATFORM host (public schema): show the platform's own
          identity. ``get_current_tenant()`` returns None on public, so
          this used to fall through to the class attributes — which
          default to ``UNFOLD_SITE_HEADER``/"Webside". The control plane
          therefore wore tenant #1's name and logo: the sidebar said
          "Webside" and the login page read "Welcome back to Webside
          Admin". Reported from production 2026-08-21.
        - Unknown schema (management command, Celery, tests): leave the
          defaults alone. Same positive-knowledge rule as
          ``BaseModelAdmin._withheld_on_public``.
        """
        context = super().each_context(request)
        from tenant.console import is_platform_console  # noqa: PLC0415
        from tenant.membership import get_current_tenant  # noqa: PLC0415

        if is_platform_console(request):
            context["site_header"] = PLATFORM_SITE_HEADER
            context["site_title"] = PLATFORM_SITE_TITLE
            context["site_subheader"] = PLATFORM_SITE_SUBHEADER
            # The tenant logo/icon lambdas resolve to webside's assets;
            # the control plane must not display a merchant's mark.
            context["site_logo"] = None
            context["site_icon"] = None
            return context

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
            path(
                "clear-cache/preview/",
                self.admin_view(self.cache_preview_view),
                name="cache-preview",
            ),
        ]
        return custom_urls + urls

    def clear_cache_view(self, request):
        if request.method == "POST":
            return self._handle_purge(request)

        from core.cache.models import CachePurgeLog

        surfaces = iter_surfaces()
        counts = CacheService.count(s.code for s in surfaces)
        groups: dict[str, list] = {}
        for surface in surfaces:
            groups.setdefault(surface.group, []).append(
                {
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
        recent_logs = CachePurgeLog.objects.select_related("actor")[:20]
        context = {
            **self.each_context(request),
            "groups": sorted(groups.items()),
            "recent_logs": recent_logs,
            "nuxt_configured": nuxt_purge_configured(),
            "title": _("Cache Management"),
        }
        return render(request, "admin/clear_cache.html", context)

    def cache_preview_view(self, request):
        """Return live counts for a comma-separated list of surface codes."""

        codes = [c for c in request.GET.get("codes", "").split(",") if c]
        counts = CacheService.count(codes)
        return JsonResponse({"counts": counts, "total": sum(counts.values())})

    def _handle_purge(self, request):
        codes = request.POST.getlist("surfaces")
        action = request.POST.get("action", "purge")
        include_related = request.POST.get("include_related") == "on"
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

        if dry_run:
            messages.info(
                request,
                _(
                    "Dry run: %(d)s Django + %(n)s Nuxt keys would be"
                    " removed across %(s)s surface(s)."
                )
                % {
                    "d": report.total_django,
                    "n": report.total_nuxt,
                    "s": len(report.surfaces),
                },
            )
        else:
            messages.success(
                request,
                _(
                    "Purged %(d)s Django + %(n)s Nuxt keys"
                    " across %(s)s surface(s)."
                )
                % {
                    "d": report.total_django,
                    "n": report.total_nuxt,
                    "s": len(report.surfaces),
                },
            )
            errors = [s for s in report.surfaces if s.nuxt_error]
            if errors:
                messages.warning(
                    request,
                    _(
                        "Nuxt purge unreachable for %(n)s surface(s)."
                        " Check NUXT_INTERNAL_BASE_URL +"
                        " NUXT_CACHE_PURGE_TOKEN."
                    )
                    % {"n": len(errors)},
                )
        return redirect("admin:clear-cache")
