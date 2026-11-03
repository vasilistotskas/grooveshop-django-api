from __future__ import annotations

from unittest import mock

import pytest
from rest_framework.test import APIClient

from builder.storefront import clear_catalogue_cache
from tenant.models import TenantMembershipRole
from tests.utils.builder import FakeCatalogue
from tests.utils.staff import (
    bind_store_tenant,
    store_staff,
    store_tenant,
    unbind_store_tenant,
)


@pytest.fixture
def catalogue(settings):
    """The storefront's catalogue endpoint, faked where ``requests`` leaves."""
    settings.NUXT_INTERNAL_BASE_URL = "http://storefront.test"
    fake = FakeCatalogue()
    clear_catalogue_cache()
    with mock.patch("builder.storefront.requests.get", side_effect=fake.get):
        yield fake
    clear_catalogue_cache()


@pytest.fixture
def tenant():
    tenant = store_tenant("builder_store")
    previous = bind_store_tenant(tenant)
    yield tenant
    unbind_store_tenant(previous)


@pytest.fixture
def features_on():
    """Every feature a block names is on, unless a test says otherwise."""
    with mock.patch("builder.services.feature_on", return_value=True) as fake:
        yield fake


@pytest.fixture
def admin(tenant):
    return store_staff(tenant, role=TenantMembershipRole.ADMIN)


@pytest.fixture
def staff(tenant):
    return store_staff(tenant, role=TenantMembershipRole.STAFF)


def api_as(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def limits(monkeypatch):
    """Replace the media service's image limits for one test: call it with
    the byte caps (a contract format name to bytes) and/or the pixel limit."""
    from core import uploads

    real = uploads.image_limits()

    def use(*, max_file_bytes=None, source_pixel_limit=None):
        uploads.image_limits.cache_clear()
        monkeypatch.setattr(
            uploads,
            "image_limits",
            lambda: uploads.ImageLimits(
                max_file_bytes=max_file_bytes or real.max_file_bytes,
                source_pixel_limit=source_pixel_limit
                or real.source_pixel_limit,
            ),
        )
        monkeypatch.setattr(
            "builder.media_services.image_limits", uploads.image_limits
        )

    return use


#: Answered by authentication before any view: no operation declares them.
UNDECLARED_BY_DESIGN = {401, 403}


@pytest.fixture(autouse=True)
def every_answer_is_in_the_schema(monkeypatch, openapi_schema):
    """Each answer a builder test provokes is declared by its operation, and
    its body satisfies the declared schema.

    The storefront generates its parsers from ``schema.yml`` and answers 422
    to a body they reject, so an undeclared status or a body that differs from
    its declaration is a bug whichever test happened upon it. This holds every
    request of this directory to that, not only the ones written for it.
    """
    import re

    from tests.utils.openapi import validate_against

    templates = [
        (re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$"), path)
        # A literal route (``media/resolve``) before a templated one it also
        # fits (``media/{id}``).
        for path in sorted(
            (p for p in openapi_schema["paths"] if "/builder/" in p),
            key=lambda p: p.count("{"),
        )
    ]
    original = APIClient.request

    def checked(self, **kwargs):
        response = original(self, **kwargs)
        path = kwargs.get("PATH_INFO", "")
        method = kwargs.get("REQUEST_METHOD", "").lower()
        template = next(
            (t for pattern, t in templates if pattern.match(path)), None
        )
        if template is None or response.status_code in UNDECLARED_BY_DESIGN:
            return response
        operation = openapi_schema["paths"][template].get(method)
        if operation is None:  # a method the route does not have: 405
            return response
        declared = operation["responses"].get(str(response.status_code))
        assert declared is not None, (
            f"{method.upper()} {template} answered {response.status_code} "
            f"({getattr(response, 'data', None)!r}), which it does not declare"
        )
        content = declared.get("content", {}).get("application/json")
        if content and response["Content-Type"].startswith("application/json"):
            validate_against(openapi_schema, content["schema"], response.json())
        return response

    monkeypatch.setattr(APIClient, "request", checked)
