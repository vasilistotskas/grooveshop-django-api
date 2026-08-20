from __future__ import annotations

from django.core.cache import cache
from django.db import connection
from django.middleware.csrf import CsrfViewMiddleware

TENANT_DOMAINS_CACHE_TTL = 300  # 5 minutes


class TenantCsrfMiddleware(CsrfViewMiddleware):
    """Dynamic CSRF trusted origins per tenant domain."""

    def _origin_verified(self, request):
        if super()._origin_verified(request):
            return True

        tenant = getattr(connection, "tenant", None)
        if tenant is None:
            return False

        origin = request.META.get("HTTP_ORIGIN", "")
        if not origin:
            return False

        # "global:" — schema-independent (tenant.cache.make_tenant_key):
        # the invalidating signal (tenant/signals.py) always fires from
        # the public schema, so a schema-prefixed key here would never
        # be found by that delete.
        cache_key = f"global:tenant_domains:{tenant.schema_name}"
        domains = cache.get(cache_key)
        if domains is None:
            domains = set(tenant.domains.values_list("domain", flat=True))
            cache.set(cache_key, domains, TENANT_DOMAINS_CACHE_TTL)

        return any(origin in (f"https://{d}", f"http://{d}") for d in domains)


class TenantCookieDomainMiddleware:
    """Per-request session/CSRF cookie domains.

    ``SESSION_COOKIE_DOMAIN`` / ``CSRF_COOKIE_DOMAIN`` are process-wide
    settings pinned to the platform apex (``.webside.gr``) — with them,
    a tenant on its OWN domain can never complete the cross-subdomain
    cookie round-trip (``tenant.com`` ↔ ``api.tenant.com``). This
    middleware rewrites the ``Domain`` attribute of the session/CSRF
    ``Set-Cookie`` headers to the value derived from the request host:

        api.acme.com  → .acme.com
        www.acme.com  → .acme.com
        acme.com      → .acme.com

    Every non-public request host IS a ``TenantDomain`` row (that's how
    the schema resolved), so stripping a single well-known service
    label and dot-prefixing yields exactly the tenant's registrable
    scope — including nested setups like ``shop.webside.gr`` →
    ``.shop.webside.gr`` staying isolated from the platform apex.

    The PUBLIC schema needs this too. The platform console used to live
    under the platform apex (``platform.webside.gr``), where the static
    ``CSRF_COOKIE_DOMAIN=.webside.gr`` happened to match — so skipping
    public looked harmless. It is not: once the console moved to its own
    domain (``platform.grooveshop.space`` — the control plane must not
    sit under tenant #1's apex), Django kept stamping ``Domain=.webside
    .gr`` on the CSRF cookie, browsers discarded it as cross-domain, and
    admin login became impossible. Derive from the request host for
    public as well.

    Hosts with no dot are internal service names (``backend-service``)
    reached by in-cluster callers; a ``Domain`` attribute is meaningless
    there, so those keep the configured value.

    MIDDLEWARE placement: directly after ``TenantMainMiddleware`` — the
    response phase runs in reverse order, so this rewrites cookies
    AFTER Session/CSRF middleware have set them.
    """

    _STRIP_LABELS = ("api.", "www.")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if not response.cookies:
            return response

        # Derive from the REQUEST HOST alone — deliberately not from
        # ``connection.tenant``. Cookie scope is a property of the host
        # the browser talked to, and the connection's tenant is not
        # reliable here: several code paths enter and exit
        # ``schema_context``/``tenant_context`` during the request, and
        # by the time the response phase unwinds ``connection.tenant``
        # may be a bare FakeTenant or None. Gating on it made this
        # middleware silently no-op on the platform console — the cookie
        # kept the settings default (``.webside.gr``) on a
        # grooveshop.space host, the browser dropped it as cross-domain,
        # and every admin POST answered 403.
        host = request.get_host().split(":")[0]
        # Internal service names carry no registrable domain.
        if "." not in host:
            return response
        for label in self._STRIP_LABELS:
            if host.startswith(label):
                host = host[len(label) :]
                break
        cookie_domain = f".{host}"

        from django.conf import settings as django_settings

        rewrite_names = {
            django_settings.SESSION_COOKIE_NAME,
            django_settings.CSRF_COOKIE_NAME,
        }
        for name, morsel in response.cookies.items():
            if name in rewrite_names:
                morsel["domain"] = cookie_domain
        return response


class TenantAwareUserSessionsMiddleware:
    """Schema-correct replacement for allauth's ``UserSessionsMiddleware``.

    ``UserSession`` rows carry an FK to the user table of whatever
    schema the request runs in. Platform staff are PUBLIC-schema
    identities (``tenant.auth_backends.PlatformStaffBackend``): on a
    tenant host their pk does not exist in that tenant's user table, so
    the stock middleware's insert dies with a ForeignKeyViolation on
    every authenticated admin request (observed live on staging
    2026-08-19). Their session rows are therefore written to the public
    schema — where their user row lives. Customer sessions keep the
    stock per-tenant behavior.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from allauth.usersessions import app_settings
        from allauth.usersessions.models import UserSession
        from django_tenants.utils import (
            get_public_schema_name,
            schema_context,
        )

        from tenant.auth_backends import is_platform_staff_session

        if (
            app_settings.TRACK_ACTIVITY
            and hasattr(request, "session")
            and request.session.session_key
            and hasattr(request, "user")
            and request.user.is_authenticated
        ):
            if is_platform_staff_session(request):
                with schema_context(get_public_schema_name()):
                    UserSession.objects.create_from_request(request)
            else:
                UserSession.objects.create_from_request(request)
        return self.get_response(request)
