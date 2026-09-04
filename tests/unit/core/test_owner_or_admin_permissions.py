"""``IsOwnerOrAdmin`` / ``IsOwnerOrAdminOrGuest`` bypass ownership only for
real store staff — never for a tenant-schema row carrying ``is_staff``."""

from __future__ import annotations

import pytest
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from core.api.permissions import IsOwnerOrAdmin, IsOwnerOrAdminOrGuest
from order.factories.order import OrderFactory
from tests.utils.staff import (
    bind_store_tenant,
    store_staff,
    store_tenant,
    unbind_store_tenant,
)
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant():
    t = store_tenant("perm_tenant")
    previous = bind_store_tenant(t)
    yield t
    unbind_store_tenant(previous)


def _request(user, **params):
    request = Request(APIRequestFactory().get("/", params))
    request.user = user
    request.parser_context = {"kwargs": {}}
    return request


@pytest.mark.parametrize("permission", [IsOwnerOrAdmin, IsOwnerOrAdminOrGuest])
def test_owner_passes(tenant, permission):
    owner = UserAccountFactory()
    order = OrderFactory(user=owner)
    assert permission().has_object_permission(_request(owner), None, order)


@pytest.mark.parametrize("permission", [IsOwnerOrAdmin, IsOwnerOrAdminOrGuest])
def test_is_staff_residue_is_refused(tenant, permission):
    order = OrderFactory(user=UserAccountFactory())
    residue = UserAccountFactory(is_staff=True)
    assert not permission().has_object_permission(
        _request(residue), None, order
    )


@pytest.mark.parametrize("permission", [IsOwnerOrAdmin, IsOwnerOrAdminOrGuest])
def test_store_staff_passes(tenant, permission):
    order = OrderFactory(user=UserAccountFactory())
    staff = store_staff(tenant)
    assert permission().has_object_permission(_request(staff), None, order)


def test_guest_order_still_needs_the_uuid(tenant):
    order = OrderFactory(user=None)
    residue = UserAccountFactory(is_staff=True)
    permission = IsOwnerOrAdminOrGuest()
    assert not permission.has_object_permission(_request(residue), None, order)
    assert permission.has_object_permission(
        _request(residue, uuid=str(order.uuid)), None, order
    )
