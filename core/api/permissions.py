from typing import override

from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import BasePermission


class IsSelfOrAdmin(BasePermission):
    message = _("You do not have permission to access this account.")

    @override
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated

    @override
    def has_object_permission(self, request, view, obj):
        return request.user.is_staff or obj.id == request.user.id
