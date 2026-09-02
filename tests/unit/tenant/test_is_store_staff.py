"""``tenant.membership.is_store_staff`` — the API's only "store staff" test.

The predicate must never read ``is_staff``: on a tenant-schema user row
that flag is customer residue from the id-preserving cutover, so a
customer carrying it has to be indistinguishable from any other
customer.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser

from tenant.membership import is_store_staff
from tenant.models import TenantMembershipRole, UserTenantMembership
from tests.utils.staff import (
    bind_store_tenant,
    stamp_platform_identity,
    store_staff,
    store_tenant,
    unbind_store_tenant,
)
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant():
    return store_tenant("isstaff_tenant")


@pytest.fixture
def other_tenant():
    return store_tenant("isstaff_other")


@pytest.fixture
def bind():
    original = None
    bound = False

    def _bind(t):
        nonlocal original, bound
        previous = bind_store_tenant(t)
        if not bound:
            original, bound = previous, True

    yield _bind
    if bound:
        unbind_store_tenant(original)


def test_anonymous_is_not_staff(tenant, bind):
    bind(tenant)
    assert is_store_staff(AnonymousUser()) is False
    assert is_store_staff(None) is False


def test_is_staff_residue_without_stamp_is_not_staff(tenant, bind):
    """A tenant-schema customer whose row carries ``is_staff`` is a customer."""
    bind(tenant)
    customer = UserAccountFactory(is_staff=True)
    assert is_store_staff(customer) is False


def test_stamp_without_membership_is_not_staff(tenant, bind):
    bind(tenant)
    user = stamp_platform_identity(UserAccountFactory(is_staff=True))
    assert is_store_staff(user) is False


def test_member_role_is_not_staff(tenant, bind):
    bind(tenant)
    user = store_staff(tenant, role=TenantMembershipRole.MEMBER)
    assert is_store_staff(user) is False


@pytest.mark.parametrize(
    "role",
    [
        TenantMembershipRole.STAFF,
        TenantMembershipRole.ADMIN,
        TenantMembershipRole.OWNER,
    ],
)
def test_staff_capable_roles_are_staff(tenant, bind, role):
    bind(tenant)
    user = store_staff(tenant, role=role)
    assert is_store_staff(user) is True


def test_inactive_membership_is_not_staff(tenant, bind):
    bind(tenant)
    user = store_staff(tenant)
    UserTenantMembership.objects.filter(user=user).update(is_active=False)
    assert is_store_staff(user) is False


def test_inactive_user_is_not_staff(tenant, bind):
    bind(tenant)
    user = store_staff(tenant, is_active=False)
    assert is_store_staff(user) is False


def test_membership_in_another_tenant_does_not_carry_over(
    tenant, other_tenant, bind
):
    user = store_staff(tenant, role=TenantMembershipRole.OWNER)
    bind(other_tenant)
    assert is_store_staff(user) is False


def test_no_bound_tenant_grants_only_superusers(bind):
    bind(None)
    staff = stamp_platform_identity(UserAccountFactory(is_staff=True))
    assert is_store_staff(staff) is False
    assert is_store_staff(UserAccountFactory(is_superuser=True)) is True


def test_superuser_passes_without_stamp(tenant, bind):
    bind(tenant)
    assert is_store_staff(UserAccountFactory(is_superuser=True)) is True


def test_answer_is_cached_per_tenant(
    tenant, other_tenant, bind, django_assert_num_queries
):
    user = store_staff(tenant)
    bind(tenant)
    assert is_store_staff(user) is True
    with django_assert_num_queries(0):
        assert is_store_staff(user) is True
    # A different tenant gets its own answer, cached separately.
    bind(other_tenant)
    assert is_store_staff(user) is False
    with django_assert_num_queries(0):
        assert is_store_staff(user) is False
