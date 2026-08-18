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


def test_public_schema_keeps_configured_domains(middleware, monkeypatch):
    monkeypatch.setattr(connection, "tenant", None, raising=False)

    request = RequestFactory().get("/", HTTP_HOST="api.webside.gr")
    response = middleware(_tenant_response())(request)

    assert (
        response.cookies[django_settings.SESSION_COOKIE_NAME]["domain"]
        == ".webside.gr"
    )
