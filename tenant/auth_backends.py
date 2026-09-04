"""Public-schema-only authentication backend for platform staff.

``UserAccount`` is mirrored per-schema (in both ``SHARED_APPS`` and
``TENANT_APPS`` — see settings.py), so the *same table name* exists in
the public schema AND in every tenant schema, distinguished only by
Postgres search_path. Platform staff/superusers are platform
identities that live in the PUBLIC schema row; this backend always
resolves against that row regardless of which schema the connection
happens to be pinned to when it runs.

Registered in ``AUTHENTICATION_BACKENDS`` — Django's session-based
``django.contrib.auth.get_user()`` refuses to restore ANY session
whose recorded backend path is not a member of that setting (see
``django/contrib/auth/__init__.py::get_user()``: ``if backend_path in
settings.AUTHENTICATION_BACKENDS``), so leaving it out would log
platform staff out on the very next request after login.

To keep it inert for the storefront's normal login *dispatch*
(``django.contrib.auth.authenticate()``, which allauth's headless
login flows call), ``authenticate()`` unconditionally returns ``None``.
That method is never used by this backend's own callers — the admin
login form (``admin.forms.PlatformAdminAuthenticationForm``) calls
``authenticate_staff()`` directly instead. The net effect: the backend
is a valid *session-restore* target (satisfies Django's allow-list
check) but can never authenticate a login attempt made through the
global dispatcher — so a shopper (or a public-schema staff member)
submitting credentials on a tenant's storefront login can never be
authenticated as the public-schema identity, even as a fallback of the
standard backend chain.
"""

from __future__ import annotations

from django.contrib.auth import BACKEND_SESSION_KEY
from django.contrib.auth.backends import BaseBackend, ModelBackend
from django_tenants.utils import get_public_schema_name, schema_context

PLATFORM_STAFF_BACKEND_PATH = "tenant.auth_backends.PlatformStaffBackend"

# Marks a user object as having been loaded from the PUBLIC schema by
# this backend — i.e. a platform identity rather than a tenant-schema
# customer that merely shares its primary key (or its email).
#
# ``TenantRolePermissionBackend`` grants nothing without it. See that
# class for why this attribute, and not the pk, is the safe signal.
PLATFORM_IDENTITY_ATTR = "_is_platform_identity"


class PlatformStaffBackend(ModelBackend):
    """Authenticates/loads platform staff against the PUBLIC schema only."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        # Deliberately inert — see module docstring. The admin login
        # form calls ``authenticate_staff()`` directly; this override
        # only exists so Django's global authenticate() dispatch (used
        # by the storefront) can never succeed through this backend.
        return None

    def authenticate_staff(self, request, username, password):
        """Authenticate admin-login credentials against the PUBLIC schema.

        Returns the ``UserAccount`` only when it is ``is_active`` AND
        ``is_staff`` — ordinary tenant customers (even if somehow
        matched by username/password) are never returned here.
        """
        with schema_context(get_public_schema_name()):
            user = super().authenticate(
                request, username=username, password=password
            )
        if user is None:
            return None
        if not (user.is_active and user.is_staff):
            return None
        setattr(user, PLATFORM_IDENTITY_ATTR, True)
        return user

    def get_user(self, user_id):
        """Restore a platform-staff session, re-checking the flag.

        ``authenticate_staff`` requires ``is_staff`` to MINT the identity
        and ``PlatformStaffTokenAuthentication.validate_user`` requires it
        to keep a token working — "an outstanding token must stop working
        the moment it does", as that code says. Session restore did
        neither: ``ModelBackend.get_user`` checks only ``is_active``.

        The stamp below is the sole thing ``is_store_staff`` and
        ``TenantRolePermissionBackend`` consult, so without this check
        the documented revocation — untick ``is_staff`` — closed the
        admin UI (which reads the flag directly) while leaving the API
        wide open to the existing session cookie for its remaining life.
        Only sessions this backend authenticated reach this method, so
        refusing a non-staff user here cannot affect a customer login.
        """
        with schema_context(get_public_schema_name()):
            user = super().get_user(user_id)
        if user is None or not user.is_staff:
            return None
        setattr(user, PLATFORM_IDENTITY_ATTR, True)
        return user


class TenantRolePermissionBackend(BaseBackend):
    """Derives model permissions from a user's role in the CURRENT tenant.

    ``UserTenantMembership`` has always described what each role may do,
    but nothing turned that into Django permissions: ``tenant_create``
    issues a membership and no Group exists anywhere. So the membership
    gate admitted a store operator to ``/admin/`` while every model page
    answered 403 — the tenant admin worked only for platform
    superusers, who bypass permission checks entirely. Reported from
    production 2026-08-21 by tenant #1's owner.

    Why permissions are COMPUTED rather than stored as Group rows:

    - ``has_perm`` is a plain string check, so no ``auth_permission``
      row needs to exist. That matters here because ``auth`` is in BOTH
      SHARED_APPS and TENANT_APPS: a Group row would live in whichever
      schema the connection happened to be pinned to, and the lookup
      would then match by ID across schemas.
    - Nothing to provision, so onboarding a store cannot forget it, and
      nothing drifts as models are added.

    Why ``PLATFORM_IDENTITY_ATTR`` and not the user's pk:

      ``UserAccount`` is mirrored per schema. A tenant-schema CUSTOMER
      can share a pk with a public-schema staff identity — the pks only
      line up today because the cutover copied users id-preserving, and
      post-cutover signups get per-schema ids. Matching on email is no
      safer: a shopper can register a tenant-schema account with a
      platform operator's address. The only sound signal is provenance,
      so this backend grants exclusively to user objects that
      ``PlatformStaffBackend`` loaded from the public schema. Admin
      sessions qualify, and so do staff API tokens
      (``tenant.api_tokens.PlatformStaffTokenAuthentication`` stamps
      the identities it resolves from public); customer storefront/API
      sessions never do.

    Platform-scope apps are never granted, at any role — see
    ``tenant.role_scopes.PLATFORM_ONLY_APP_LABELS``. The narrow grants
    ADMIN/OWNER receive over their own Tenant row and their own team are
    object-scoped in the ModelAdmin, not here; this layer only decides
    that the model class is reachable at all.
    """

    def authenticate(self, request, **kwargs):
        """Never authenticates — this backend only answers permissions."""
        return

    def get_all_permissions(self, user_obj, obj=None) -> set[str]:
        # Object-level permissions are not modelled here; returning an
        # empty set lets other backends answer object checks.
        if obj is not None:
            return set()
        if not getattr(user_obj, "is_active", False):
            return set()
        # Provenance gate — see class docstring.
        if not getattr(user_obj, PLATFORM_IDENTITY_ATTR, False):
            return set()

        from tenant.membership import (
            get_current_tenant,
            get_membership,
        )

        tenant = get_current_tenant()
        if tenant is None:
            return set()

        # Cache per (user object, tenant). Keyed by tenant because one
        # process serves many tenants and a user may hold different
        # roles in each; an unkeyed cache would leak the first tenant's
        # answer into the next request handled on the same object.
        cache_attr = f"_tenant_role_perms_{tenant.pk}"
        cached = getattr(user_obj, cache_attr, None)
        if cached is not None:
            return cached

        membership = get_membership(user_obj, tenant)
        perms = (
            _permissions_for_role(membership.role)
            if membership is not None
            else set()
        )
        setattr(user_obj, cache_attr, perms)
        return perms

    def has_perm(self, user_obj, perm, obj=None) -> bool:
        return perm in self.get_all_permissions(user_obj, obj)

    def has_module_perms(self, user_obj, app_label) -> bool:
        prefix = f"{app_label}."
        return any(
            perm.startswith(prefix)
            for perm in self.get_all_permissions(user_obj)
        )


def _permissions_for_apps(
    app_labels: frozenset[str] | set[str],
    actions: tuple[str, ...] | None = None,
) -> set[str]:
    """Every permission codename owned by *app_labels*.

    Built from the app registry rather than the ``auth_permission``
    table so the result does not depend on which schema is on the
    search path — and so a model added later is covered without a
    migration or a data fixture.
    """
    from django.apps import apps as django_apps
    from django.contrib.auth import get_permission_codename

    perms: set[str] = set()
    for model in django_apps.get_models():
        opts = model._meta
        if opts.app_label not in app_labels:
            continue
        allowed = actions or opts.default_permissions
        for action in opts.default_permissions:
            if action not in allowed:
                continue
            perms.add(
                f"{opts.app_label}.{get_permission_codename(action, opts)}"
            )
        # Custom Meta.permissions are store-scope too, but only for
        # roles that get the full action set — a custom permission has
        # no action to compare against, so it cannot be filtered.
        if actions is None:
            for codename, _label in opts.permissions:
                perms.add(f"{opts.app_label}.{codename}")
    return perms


def _permissions_for_role(role: str) -> set[str]:
    """The permission set a role grants inside its tenant."""
    from tenant.models import TenantMembershipRole
    from tenant.role_scopes import (
        operational_app_labels,
        store_app_labels,
    )

    if role == TenantMembershipRole.STAFF:
        # "can view the tenant's operational admin (orders, products)
        # but cannot change tenant settings or invite other staff"
        # (TenantMembershipRole). Delete is withheld as well: it is the
        # irreversible action, and nothing in that description implies
        # it.
        return _permissions_for_apps(
            operational_app_labels(), actions=("view", "add", "change")
        )

    if role in (TenantMembershipRole.ADMIN, TenantMembershipRole.OWNER):
        perms = _permissions_for_apps(store_app_labels())
        # Narrow, deliberate exceptions to "platform scope is never
        # granted": a store's own row and its own team. Object scoping
        # (own tenant only) is enforced in the ModelAdmin — this only
        # makes the pages reachable. add/delete Tenant and anything on
        # TenantDomain stay platform-only.
        perms |= {
            "tenant.view_tenant",
            "tenant.change_tenant",
            "tenant.view_usertenantmembership",
            "tenant.add_usertenantmembership",
            "tenant.change_usertenantmembership",
            "tenant.delete_usertenantmembership",
        }
        return perms

    # MEMBER (retired) and anything unrecognised grant nothing.
    return set()


def is_platform_staff_session(request) -> bool:
    """True if the current session was authenticated via ``PlatformStaffBackend``.

    Used by ``MyAdminSite.has_permission()`` to close a pk-collision
    ambiguity: ``UserAccount`` primary keys are NOT guaranteed distinct
    across schemas (a tenant-schema customer could coincidentally share
    a pk with a public-schema staff membership). Requiring the SESSION
    to have been minted by ``PlatformStaffBackend`` — which only ever
    authenticates public-schema, is_staff users — closes that gap
    regardless of what ``request.user``'s pk happens to collide with.
    """
    session = getattr(request, "session", None)
    if session is None:
        return False
    return session.get(BACKEND_SESSION_KEY) == PLATFORM_STAFF_BACKEND_PATH
