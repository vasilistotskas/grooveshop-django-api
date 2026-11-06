"""A guest order's uuid is a key, and it works for a limited time.

It reads the customer's name, email, phone and address without a login, so a
key that lasts for ever outlives every place it has been seen.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory

from core.api.permissions import IsOwnerOrAdminOrGuest
from order.models.order import Order
from order.views.order import OrderViewSet


@pytest.fixture(autouse=True)
def _window(settings):
    settings.GUEST_ORDER_ACCESS_DAYS = 14


def _order(age_days: float = 0) -> Order:
    return Order(
        uuid=uuid4(),
        user=None,
        created_at=timezone.now() - timedelta(days=age_days),
    )


class TestOpensForGuest:
    def test_a_recent_order_opens_with_its_uuid(self):
        order = _order(age_days=13)
        assert order.opens_for_guest(str(order.uuid)) is True

    def test_an_older_order_does_not(self):
        order = _order(age_days=15)
        assert order.opens_for_guest(str(order.uuid)) is False

    @pytest.mark.parametrize("wrong", [None, "", "not-the-uuid"])
    def test_a_wrong_uuid_does_not(self, wrong):
        assert _order().opens_for_guest(wrong) is False

    def test_it_accepts_the_uuid_object_a_url_converter_gives(self):
        order = _order()
        assert order.opens_for_guest(order.uuid) is True


class TestTheTwoPlacesThatCheckIt:
    def _permission(self, order: Order, uuid: object) -> bool:
        request = APIRequestFactory().get("/", {"uuid": str(uuid)})
        request.query_params = request.GET
        request.user = None
        request.parser_context = {"kwargs": {}}
        return IsOwnerOrAdminOrGuest().has_object_permission(
            request, None, order
        )

    def test_the_shared_permission_refuses_an_old_order(self):
        old = _order(age_days=15)
        assert self._permission(old, old.uuid) is False

    def test_the_shared_permission_opens_a_recent_order(self):
        recent = _order(age_days=1)
        assert self._permission(recent, recent.uuid) is True

    def _view_check(self, order: Order) -> None:
        view = OrderViewSet()
        view.action = "retrieve_by_uuid"
        view.kwargs = {"uuid": str(order.uuid)}
        request = APIRequestFactory().get("/")
        request.query_params = request.GET
        request.user = None
        view.check_object_permissions(request, order)

    def test_the_order_views_refuse_an_old_order(self):
        with pytest.raises(PermissionDenied):
            self._view_check(_order(age_days=15))

    def test_the_order_views_open_a_recent_order(self):
        self._view_check(_order(age_days=1))
