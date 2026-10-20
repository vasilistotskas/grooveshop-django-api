"""Rows on the retired MEMBER role get an explicit role, and no access.

MEMBER granted nothing, yet the model's default kept creating it, so a
store can hold active rows that look like team members and are not.
Moving them to STAFF while switching them off makes every row carry a
real role without handing anyone access they did not have.
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps

from tenant.models import TenantMembershipRole, UserTenantMembership
from tests.utils.staff import store_tenant
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

MIGRATION = "tenant.migrations.0057_retire_member_role"


def _run():
    importlib.import_module(MIGRATION).member_rows_to_inactive_staff(apps, None)


def _row(tenant, role, *, is_active=True):
    return UserTenantMembership.objects.create(
        user=UserAccountFactory(is_staff=True),
        tenant=tenant,
        role=role,
        is_active=is_active,
    )


def test_a_member_row_becomes_inactive_staff():
    retired = _row(store_tenant("retire_member"), "member")

    _run()

    retired.refresh_from_db()
    assert retired.role == TenantMembershipRole.STAFF
    assert retired.is_active is False


def test_issued_roles_are_left_alone():
    tenant = store_tenant("retire_member_other")
    owner = _row(tenant, TenantMembershipRole.OWNER)
    staff = _row(tenant, TenantMembershipRole.STAFF)

    _run()

    owner.refresh_from_db()
    staff.refresh_from_db()
    assert (owner.role, owner.is_active) == (TenantMembershipRole.OWNER, True)
    assert (staff.role, staff.is_active) == (TenantMembershipRole.STAFF, True)
