from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import BasePermission, DjangoModelPermissions

from tenant.membership import is_platform_superuser, is_store_staff

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

    The API's notion of "store staff" is therefore
    ``tenant.membership.is_store_staff`` — a provenance-stamped
    platform identity with a staff-capable membership in the current
    tenant (see ``docs/api-staff-identity.md``) — and ``is_staff`` is
    never consulted on an API request. This class is the stricter,
    platform-only gate for surfaces that no store operator should
    reach.

    ``is_superuser`` gets the same treatment as ``is_staff``, which it
    did not used to. It is the same column family on the same copied
    rows, so the argument above applies to it verbatim: this class
    rejected ``is_staff`` for being untrustworthy on a tenant-schema row
    and then trusted ``is_superuser`` on that same row.
    ``is_platform_superuser`` honours the flag only when the identity
    provably came from the public schema.
    """

    message = _("Only platform superusers may perform this action.")

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and is_platform_superuser(user)
        )


class IsStoreStaff(BasePermission):
    """Staff of the tenant on this connection — the predicate, not a model.

    ``StoreStaffModelPermissions`` is the right gate for a ViewSet: it
    maps the HTTP method to a model permission. It cannot be used on a
    function-based ``@api_view``, which has no queryset for
    ``DjangoModelPermissions`` to read — that combination raises
    ``AssertionError`` and answers 500.

    This is the plain predicate for those endpoints: per-store data that
    a store's own staff have a legitimate claim to, as distinct from
    ``IsPlatformSuperuser``, which is for surfaces no store operator
    should reach at all.
    """

    message = _("Only store staff may perform this action.")

    def has_permission(self, request, view) -> bool:
        return is_store_staff(getattr(request, "user", None))


class StoreStaffModelPermissions(DjangoModelPermissions):
    """Role-derived permissions for STORE-scoped administrative routes.

    DRF's own ``DjangoModelPermissions`` does the whole job once the
    identity is right: it maps the HTTP method to a model permission
    codename and asks ``user.has_perm(...)`` — which, for a user object
    stamped by ``PlatformStaffTokenAuthentication``, resolves through
    ``TenantRolePermissionBackend``: the SAME policy the admin uses
    (``tenant.role_scopes``). One policy source; the API and the admin
    cannot drift.

    Who passes, concretely:

    - A **platform superuser** — ``has_perm`` short-circuits before any
      backend.
    - A **staff token holder with a role in the CURRENT tenant** — the
      role backend derives their set from ``connection.tenant`` +
      membership. STAFF gets view/add/change on operational apps,
      ADMIN/OWNER the full store scope.
    - A **customer session: never.** Unstamped identities get nothing
      from the role backend, customers hold no Django perms, and the
      tenant-schema privilege flags were cleared — three independent
      reasons.

    The only extension over stock ``DjangoModelPermissions`` is
    requiring the ``view`` permission on reads: these are
    administrative routes whose LIST/RETRIEVE variants are separately
    exposed as public endpoints where reading is intended; here a read
    is staff activity like any other.
    """

    perms_map = {
        **DjangoModelPermissions.perms_map,
        "GET": ["%(app_label)s.view_%(model_name)s"],
        "HEAD": ["%(app_label)s.view_%(model_name)s"],
    }


class StoreStaffChangePermission(BasePermission):
    """``change`` permission on the view's model, for custom actions.

    ``DjangoModelPermissions`` maps by HTTP method, and custom
    operational actions are POSTs — which would map to ``add``. A
    refund, a tracking update or a carrier cancel is not "adding an
    order"; it is CHANGING one, and STAFF's role text ("cannot
    delete") only holds if these map to ``change``. Used from
    ``get_permissions`` for exactly those actions.
    """

    message = _("You do not have permission to manage this resource.")

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated):
            return False
        queryset = getattr(view, "queryset", None)
        if queryset is None:
            queryset = view.get_queryset()
        opts = queryset.model._meta
        return user.has_perm(f"{opts.app_label}.change_{opts.model_name}")


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
        if is_store_staff(request.user):
            return True

        return self._is_owner(request.user, obj)


class IsOwnerOrAdminOrGuest(IsOwnerMixin, BasePermission):
    message = _("You do not have permission to access this object.")

    def has_permission(self, request, view):
        return True

    def has_object_permission(self, request, view, obj):
        if is_store_staff(request.user):
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
