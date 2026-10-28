from __future__ import annotations

from unittest import mock

import pytest
from django.test import Client

from tenant.models import TenantMembershipRole
from tests.utils.staff import (
    bind_store_tenant,
    store_staff,
    store_tenant,
    unbind_store_tenant,
)
from tests.utils.texts import FakeStorefront
from texts.storefront import clear_catalogue_cache

STAFF_BACKEND = "tenant.auth_backends.PlatformStaffBackend"


@pytest.fixture
def stub(settings):
    """The storefront's text endpoints, faked where ``requests`` leaves."""
    settings.NUXT_INTERNAL_BASE_URL = "http://storefront.test"
    fake = FakeStorefront()
    clear_catalogue_cache()
    with (
        mock.patch("texts.storefront.requests.get", side_effect=fake.get),
        mock.patch("texts.storefront.requests.post", side_effect=fake.post),
    ):
        yield fake
    clear_catalogue_cache()


@pytest.fixture
def tenant():
    tenant = store_tenant("texts_store")
    previous = bind_store_tenant(tenant)
    yield tenant
    unbind_store_tenant(previous)


def staff_client(tenant, role=TenantMembershipRole.ADMIN):
    user = store_staff(tenant, role=role)
    client = Client()
    client.force_login(user, backend=STAFF_BACKEND)
    return client, user


@pytest.fixture
def admin_user(tenant):
    return store_staff(tenant, role=TenantMembershipRole.ADMIN)


@pytest.fixture
def client_and_user(tenant):
    return staff_client(tenant)
