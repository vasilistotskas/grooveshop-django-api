from __future__ import annotations

from os import getenv

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import path
from django.utils.translation import gettext_lazy as _
from unfold.sites import UnfoldAdminSite

from core.cache import CacheService
from core.cache.nuxt import is_configured as nuxt_purge_configured
from core.cache.registry import iter_surfaces


class MyAdminSite(UnfoldAdminSite):
    site_header = getenv("UNFOLD_SITE_HEADER", "Webside")
    site_title = getenv("UNFOLD_SITE_TITLE", "Webside Admin")
    index_title = _("Dashboard")

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
