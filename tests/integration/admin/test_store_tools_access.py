"""The store admin's tools menu and the cache page follow permissions.

Unfold renders ``SITE_DROPDOWN`` items without a permission check; the
static list showed every store's staff the cache purge, a Rosetta link
that is refused outside the public schema, and the platform's ops
consoles.
"""

from __future__ import annotations

import pytest
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse

from admin.navigation import store_site_dropdown
from tenant.models import TenantMembershipRole
from tests.utils.staff import (
    bind_store_tenant,
    stamp_platform_identity,
    store_staff,
    store_tenant,
    unbind_store_tenant,
)
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

OPS = [{"icon": "router", "title": "RabbitMQ", "link": "http://mq.test/"}]


@pytest.fixture
def tenant():
    tenant = store_tenant("tools_access")
    previous = bind_store_tenant(tenant)
    yield tenant
    unbind_store_tenant(previous)


def _titles(user) -> set[str]:
    request = RequestFactory().get("/admin/")
    request.user = user
    return {str(item["title"]) for item in store_site_dropdown(request)}


@override_settings(ADMIN_OPS_LINKS=OPS, LANGUAGE_CODE="en")
def test_store_staff_sees_only_its_tools(tenant):
    titles = _titles(store_staff(tenant, role=TenantMembershipRole.STAFF))

    assert "Email Templates" in titles
    assert "API Swagger" in titles
    assert not titles & {"Cache", "Rosetta", "RabbitMQ"}


@override_settings(ADMIN_OPS_LINKS=OPS, LANGUAGE_CODE="en")
def test_a_store_admin_may_purge_its_caches(tenant):
    titles = _titles(store_staff(tenant, role=TenantMembershipRole.ADMIN))

    assert "Cache" in titles
    assert "RabbitMQ" not in titles


@override_settings(ADMIN_OPS_LINKS=OPS, LANGUAGE_CODE="en")
def test_a_platform_superuser_also_gets_the_ops_consoles(tenant):
    superuser = stamp_platform_identity(UserAccountFactory(admin=True))

    assert {"Cache", "RabbitMQ"} <= _titles(superuser)


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
