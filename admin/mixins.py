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
