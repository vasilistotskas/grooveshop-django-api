"""The cache page follows the purge permission, not the admin login.

Which tools each role is offered is pinned with the sidebar
(``test_store_navigation.py``); this is the page itself refusing a
typed URL.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from tenant.models import TenantMembershipRole
from tests.utils.staff import (
    bind_store_tenant,
    store_staff,
    store_tenant,
    unbind_store_tenant,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant():
    tenant = store_tenant("tools_access")
    previous = bind_store_tenant(tenant)
    yield tenant
    unbind_store_tenant(previous)


@pytest.mark.parametrize(
    ("role", "status"),
    [(TenantMembershipRole.STAFF, 403), (TenantMembershipRole.ADMIN, 200)],
)
def test_the_cache_page_needs_the_purge_permission(tenant, role, status):
    client = Client()
    client.force_login(
        store_staff(tenant, role=role),
        backend="tenant.auth_backends.PlatformStaffBackend",
    )

    assert client.get(reverse("admin:clear-cache")).status_code == status
