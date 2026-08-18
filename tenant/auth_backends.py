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
from django.contrib.auth.backends import ModelBackend
from django_tenants.utils import get_public_schema_name, schema_context

PLATFORM_STAFF_BACKEND_PATH = "tenant.auth_backends.PlatformStaffBackend"


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
        return user

    def get_user(self, user_id):
        with schema_context(get_public_schema_name()):
            return super().get_user(user_id)


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
