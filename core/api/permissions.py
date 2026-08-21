from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import BasePermission
from django.contrib.auth import get_user_model

User = get_user_model()


class IsPlatformSuperuser(BasePermission):
    """Administrative API surfaces: platform superusers only.

    Replaces DRF's ``IsAdminUser`` on every administrative endpoint,
    because ``IsAdminUser`` is literally ``user.is_staff`` and that flag
    does not mean what it appears to mean here.

    ``knox`` is in TENANT_APPS only, so an API token is a per-schema
    row and ``request.user`` on any API call is a user from THAT
    store's schema (see ``core.api.tokens.BoundedTokenAuthentication``).
    ``is_staff`` on such a row is therefore a per-store flag on a
    CUSTOMER record — and after the multi-tenant cutover copied users
    id-preserving, several customer rows carried it purely as
    residue. ``IsAdminUser`` handed those accounts administrative API
    rights over the store's catalogue.

    Membership cannot be used here the way it is in the admin:
    ``UserTenantMembership.user`` is an FK to
    ``public.user_useraccount``, so matching it against a tenant-schema
    user compares primary keys ACROSS schemas — the same collision the
    admin closes with a provenance stamp
    (``tenant.auth_backends.PLATFORM_IDENTITY_ATTR``), which an API
    session can never carry.

    So the API has no sound notion of "store staff", and these
    endpoints have no first-party consumer: the storefront never writes
    catalogue resources and the agent gateway only reads them. Store
    operators administer their store through the Django admin, where
    role-derived permissions apply properly.

    Granting store operators programmatic write access needs a real
    staff identity for the API — public-schema authentication plus
    membership and role, mirroring the admin. That design is recorded
    in ``docs/api-staff-identity.md``; until it exists, administrative
    API routes stay platform-only rather than resting on a flag nobody
    manages.
    """

    message = _("Only platform superusers may perform this action.")

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and user.is_superuser
        )


class IsOwnerMixin:
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


class IsOwnerOrAdmin(IsOwnerMixin, BasePermission):
    message = _("You do not have permission to access this object.")

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True

        return self._is_owner(request.user, obj)


class IsOwnerOrAdminOrGuest(IsOwnerMixin, BasePermission):
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
                # Guest orders require UUID verification
                request_uuid = request.query_params.get(
                    "uuid"
                ) or request.parser_context.get("kwargs", {}).get("uuid")
                return bool(request_uuid and str(obj.uuid) == str(request_uuid))

            if request.user and request.user.is_authenticated:
                return obj.user == request.user

        if request.user and request.user.is_authenticated:
            return self._is_owner(request.user, obj)

        return False
