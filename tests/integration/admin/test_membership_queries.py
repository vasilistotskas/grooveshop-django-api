"""A store staff member's admin page reads their membership once.

``MyAdminSite.has_permission`` runs several times per admin request
(the view gate, ``each_context``, the app list), and the permission
backend and ``is_store_staff`` need the same row again. Each used to
run its own SELECT.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
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
def staff_client():
    tenant = store_tenant("membership_memo")
    previous = bind_store_tenant(tenant)
    user = store_staff(tenant, role=TenantMembershipRole.ADMIN)
    client = Client()
    client.force_login(
        user, backend="tenant.auth_backends.PlatformStaffBackend"
    )
    yield client
    unbind_store_tenant(previous)


def test_one_membership_select_per_admin_page(staff_client):
    url = reverse("admin:order_order_changelist")
    staff_client.get(url)
    with CaptureQueriesContext(connection) as queries:
        response = staff_client.get(url)

    assert response.status_code == 200
    membership_selects = [
        query["sql"]
        for query in queries.captured_queries
        if 'FROM "tenant_usertenantmembership"' in query["sql"]
    ]
    assert len(membership_selects) == 1, membership_selects
