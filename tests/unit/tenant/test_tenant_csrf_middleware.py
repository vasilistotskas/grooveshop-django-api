"""``TenantCsrfMiddleware`` widens the trusted origins to the current
store's own domains and weakens nothing else: a request from the
store's origin still needs a valid CSRF token, and a foreign origin is
refused even with one.

CodeQL's ``py/csrf-protection-disabled`` matches the literal
``django.middleware.csrf.CsrfViewMiddleware`` in ``MIDDLEWARE`` and so
reports this subclass as CSRF being off; these tests are what that
report is checked against."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.middleware.csrf import CsrfViewMiddleware, get_token
from django.test import RequestFactory

from tenant.middleware import TenantCsrfMiddleware
from tenant.models import TenantDomain
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)

pytestmark = pytest.mark.django_db

STORE_HOST = "api.shop.csrf-tenant.example"
STORE_ORIGIN = "https://shop.csrf-tenant.example"
FOREIGN_ORIGIN = "https://evil.example"


@pytest.fixture
def tenant():
    t = store_tenant("csrf_tenant")
    TenantDomain.objects.create(
        tenant=t, domain="shop.csrf-tenant.example", is_primary=True
    )
    previous = bind_store_tenant(t)
    yield t
    unbind_store_tenant(previous)


def _view(request):
    return HttpResponse("ok")


def _post(origin: str, *, with_token: bool):
    request = RequestFactory().post(
        "/api/v1/cart",
        secure=True,
        HTTP_HOST=STORE_HOST,
        HTTP_ORIGIN=origin,
    )
    if with_token:
        # What a browser sends: the cookie secret, and the masked token
        # the page read from it.
        request.META[settings.CSRF_HEADER_NAME] = get_token(request)
        request.COOKIES[settings.CSRF_COOKIE_NAME] = request.META["CSRF_COOKIE"]
    middleware = TenantCsrfMiddleware(_view)
    middleware.process_request(request)
    return middleware.process_view(request, _view, (), {})


def test_it_is_the_csrf_middleware_in_the_stack():
    assert issubclass(TenantCsrfMiddleware, CsrfViewMiddleware)
    assert "tenant.middleware.TenantCsrfMiddleware" in settings.MIDDLEWARE
    # Not alongside Django's own, which would refuse the store's origin.
    assert (
        "django.middleware.csrf.CsrfViewMiddleware" not in settings.MIDDLEWARE
    )


def test_the_stores_origin_with_a_token_is_accepted(tenant):
    assert _post(STORE_ORIGIN, with_token=True) is None


def test_the_stores_origin_still_needs_a_token(tenant):
    response = _post(STORE_ORIGIN, with_token=False)

    assert response is not None
    assert response.status_code == 403


def test_a_foreign_origin_is_refused_even_with_a_token(tenant):
    response = _post(FOREIGN_ORIGIN, with_token=True)

    assert response is not None
    assert response.status_code == 403


def test_without_a_store_only_the_static_origins_count():
    response = _post(STORE_ORIGIN, with_token=True)

    assert response is not None
    assert response.status_code == 403
