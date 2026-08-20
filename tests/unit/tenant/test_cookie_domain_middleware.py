"""TenantCookieDomainMiddleware: per-request session/CSRF cookie scope."""

from __future__ import annotations

import pytest
from django.conf import settings as django_settings
from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory

from tenant.middleware import TenantCookieDomainMiddleware


@pytest.fixture
def middleware():
    def _build(response: HttpResponse):
        return TenantCookieDomainMiddleware(lambda _request: response)

    return _build


def _tenant_response() -> HttpResponse:
    response = HttpResponse()
    response.set_cookie(
        django_settings.SESSION_COOKIE_NAME, "abc", domain=".webside.gr"
    )
    response.set_cookie(
        django_settings.CSRF_COOKIE_NAME, "tok", domain=".webside.gr"
    )
    response.set_cookie("unrelated", "x", domain=".webside.gr")
    return response


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("acme.example", ".acme.example"),
        ("api.acme.example", ".acme.example"),
        ("www.acme.example", ".acme.example"),
        ("shop.platform.example", ".shop.platform.example"),
    ],
)
def test_rewrites_session_and_csrf_domains(
    middleware, monkeypatch, tenant_factory, host, expected
):
    tenant = tenant_factory(f"cookie-{host.replace('.', '-')}"[:40])
    monkeypatch.setattr(connection, "tenant", tenant, raising=False)

    request = RequestFactory().get("/", HTTP_HOST=host)
    response = middleware(_tenant_response())(request)

    assert (
        response.cookies[django_settings.SESSION_COOKIE_NAME]["domain"]
        == expected
    )
    assert (
        response.cookies[django_settings.CSRF_COOKIE_NAME]["domain"] == expected
    )
    # Other cookies keep whatever the view set.
    assert response.cookies["unrelated"]["domain"] == ".webside.gr"


@pytest.mark.parametrize("tenant_state", [None, "public", "tenant"])
def test_scope_follows_the_host_whatever_the_connection_holds(
    middleware, monkeypatch, tenant_state
):
    """Cookie scope is a property of the HOST, not of connection.tenant.

    Gating on the connection is what broke the platform console: by the
    time the response phase unwinds, the schema_context/tenant_context
    wrappers used during the request may have left connection.tenant as
    a bare FakeTenant or None, so the rewrite silently did not happen
    and the cookie kept the settings default on a foreign apex.
    """
    if tenant_state is None:
        monkeypatch.setattr(connection, "tenant", None, raising=False)
    else:
        obj = type("_T", (), {"schema_name": tenant_state})()
        monkeypatch.setattr(connection, "tenant", obj, raising=False)

    request = RequestFactory().get(
        "/admin/login/", HTTP_HOST="platform.grooveshop.space"
    )
    response = middleware(_tenant_response())(request)

    assert (
        response.cookies[django_settings.CSRF_COOKIE_NAME]["domain"]
        == ".platform.grooveshop.space"
    )


class _PublicTenant:
    schema_name = "public"


def test_public_schema_derives_from_its_own_host(middleware, monkeypatch):
    """The platform console is not exempt.

    It used to live under the platform apex, where the static
    ``CSRF_COOKIE_DOMAIN=.webside.gr`` happened to match, so skipping
    public looked free. Once the console moved to its own domain the
    static value was cross-domain, browsers dropped the CSRF cookie and
    admin login returned 403 on every POST — observed live on
    platform-staging.grooveshop.space.
    """
    monkeypatch.setattr(connection, "tenant", _PublicTenant(), raising=False)

    request = RequestFactory().get(
        "/admin/login/", HTTP_HOST="platform-staging.grooveshop.space"
    )
    response = middleware(_tenant_response())(request)

    expected = ".platform-staging.grooveshop.space"
    assert (
        response.cookies[django_settings.CSRF_COOKIE_NAME]["domain"] == expected
    )
    assert (
        response.cookies[django_settings.SESSION_COOKIE_NAME]["domain"]
        == expected
    )


def test_internal_service_host_keeps_configured_domains(
    middleware, monkeypatch
):
    """``backend-service`` has no registrable domain to scope to."""
    monkeypatch.setattr(connection, "tenant", _PublicTenant(), raising=False)

    request = RequestFactory().get("/", HTTP_HOST="backend-service")
    response = middleware(_tenant_response())(request)

    assert (
        response.cookies[django_settings.CSRF_COOKIE_NAME]["domain"]
        == ".webside.gr"
    )
