"""The agent gateway's own order read (``/api/v1/agent/internal/order``)."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from order.enum.attribution import OrderSourceType
from order.factories.order import OrderFactory
from order.models.attribution import OrderAttribution
from order.models.order import Order
from user.factories.account import UserAccountFactory

SECRET = "gateway-secret"


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET=SECRET)
class AgentInternalOrderTests(APITestCase):
    def read(self, uuid, token: str | None = SECRET):
        headers = {} if token is None else {"X-Internal-Token": token}
        return self.client.get(
            reverse("agent-internal-order", kwargs={"uuid": uuid}),
            headers=headers,
        )

    def agent_order(self, **kwargs):
        order = OrderFactory(**kwargs)
        OrderAttribution.objects.create(
            order=order, source_type=OrderSourceType.AGENT, source="ucp"
        )
        return order

    def aged_guest_order(self):
        order = self.agent_order(user=None)
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.now()
            - timedelta(days=settings.GUEST_ORDER_ACCESS_DAYS + 1)
        )
        return order

    def test_reads_a_guest_order_past_its_uuid_limit(self):
        order = self.aged_guest_order()

        guest = self.client.get(
            reverse("order-retrieve-by-uuid", kwargs={"uuid": order.uuid})
        )
        response = self.read(order.uuid)

        self.assertEqual(guest.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["uuid"], str(order.uuid))

    def test_reads_an_order_that_has_an_owner(self):
        order = self.agent_order(user=UserAccountFactory())

        response = self.read(order.uuid)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["uuid"], str(order.uuid))

    def test_serves_no_order_an_agent_did_not_place(self):
        unattributed = OrderFactory(user=None)
        from_the_web = OrderFactory(user=None)
        OrderAttribution.objects.create(
            order=from_the_web, source_type=OrderSourceType.DIRECT
        )

        for order in (unattributed, from_the_web):
            with self.subTest(order=order.pk):
                response = self.read(order.uuid)
                self.assertEqual(
                    response.status_code, status.HTTP_404_NOT_FOUND
                )

    def test_hides_itself_without_the_secret(self):
        order = self.agent_order(user=None)

        for token in (None, "", "wrong", "caf\xe9"):
            with self.subTest(token=token):
                response = self.read(order.uuid, token)
                self.assertEqual(
                    response.status_code, status.HTTP_404_NOT_FOUND
                )
                self.assertNotIn("uuid", response.json())

    @override_settings(AGENT_GATEWAY_INTERNAL_SECRET="")
    def test_an_unset_secret_opens_nothing(self):
        order = self.agent_order(user=None)

        response = self.read(order.uuid, "")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_an_unknown_order_is_not_found(self):
        response = self.read(uuid4())

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
