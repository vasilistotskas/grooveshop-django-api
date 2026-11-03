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
