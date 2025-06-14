from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import BasePermission


class IsSelfOrAdmin(BasePermission):
    message = _("You do not have permission to access this account.")

    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        return request.user.is_staff or obj.id == request.user.id


class IsOwnerOrReadOnly(BasePermission):
    message = _("You can only modify your own content.")

    def has_object_permission(self, request, view, obj):
        if request.method in ["GET", "HEAD", "OPTIONS"]:
            return True
        return hasattr(obj, "user") and obj.user == request.user


class IsStaffOrReadOnly(BasePermission):
    message = _("Only staff members can perform this action.")

    def has_permission(self, request, view):
        if request.method in ["GET", "HEAD", "OPTIONS"]:
            return True
        return request.user and request.user.is_staff


class IsAuthenticatedOrReadOnly(BasePermission):
    message = _("Authentication required for this action.")

    def has_permission(self, request, view):
        if request.method in ["GET", "HEAD", "OPTIONS"]:
            return True
        return request.user and request.user.is_authenticated
