from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import BasePermission
from django.contrib.auth import get_user_model

User = get_user_model()


class IsOwnerOrAdmin(BasePermission):
    message = _("You do not have permission to access this object.")

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True

        return self._is_owner(request.user, obj)

    def _is_owner(self, user, obj):
        if hasattr(obj, "user"):
            return obj.user == user

        if self._is_user_instance(obj):
            return obj.id == user.id

        if hasattr(obj, "owner"):
            return obj.owner == user

        if hasattr(obj, "created_by"):
            return obj.created_by == user

        return False

    def _is_user_instance(self, obj):
        return isinstance(obj, User)


class IsOwnerOrAdminOrGuest(BasePermission):
    message = _("You do not have permission to access this object.")

    def has_permission(self, request, view):
        return True

    def has_object_permission(self, request, view, obj):
        if (
            request.user
            and request.user.is_authenticated
            and request.user.is_staff
        ):
            return True

        if hasattr(obj, "user"):
            if obj.user is None:
                return True

            if request.user and request.user.is_authenticated:
                return obj.user == request.user

        if request.user and request.user.is_authenticated:
            return self._is_owner(request.user, obj)

        return False

    def _is_owner(self, user, obj):
        if hasattr(obj, "user"):
            return obj.user == user

        if isinstance(obj, User):
            return obj.id == user.id

        if hasattr(obj, "owner"):
            return obj.owner == user

        if hasattr(obj, "created_by"):
            return obj.created_by == user

        return False
