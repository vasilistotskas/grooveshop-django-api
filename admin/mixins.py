"""Reusable admin permission mixins."""

from __future__ import annotations


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
