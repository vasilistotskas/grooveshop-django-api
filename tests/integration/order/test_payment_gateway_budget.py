"""The payment budget of an agent gateway call, through the real route.

Every payment route authorizes the order its `pk` names and checks that
`?uuid=` opens it (`OrderViewSet.check_object_permissions`). The
throttle reads the same two sources (`parser_context` kwargs and the
query), so a call budgets the order the route authorizes: one order's
calls cannot spend another's, nor can a uuid that opens nothing.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from core.api.throttling import (
    PaymentAttemptAnonThrottle,
    PaymentAttemptThrottle,
)
from order.factories.order import OrderFactory
from tests.utils.redis import private_cache

SECRET = "gateway-shared-secret"


@pytest.fixture
def gateway(monkeypatch, settings) -> APIClient:
    settings.AGENT_GATEWAY_INTERNAL_SECRET = SECRET
    for throttle in (PaymentAttemptThrottle, PaymentAttemptAnonThrottle):
        monkeypatch.setattr(throttle, "rate", "1/minute", raising=False)
        monkeypatch.setattr(throttle, "cache", private_cache(), raising=False)
    client = APIClient()
    client.credentials(HTTP_X_INTERNAL_GATEWAY=SECRET)
    return client


def _pay(client: APIClient, order, uuid_value=None) -> int:
    url = reverse("order-create-payment-intent", kwargs={"pk": order.pk})
    return client.post(
        f"{url}?uuid={uuid_value or order.uuid}", format="json"
    ).status_code


@pytest.mark.django_db
class TestPaymentBudgetFollowsTheRoute:
    def test_an_order_has_its_own_budget(self, gateway):
        one, two = OrderFactory(user=None), OrderFactory(user=None)

        assert _pay(gateway, one) != 429
        assert _pay(gateway, one) == 429
        assert _pay(gateway, two) != 429

    def test_another_orders_uuid_spends_nothing_of_it(self, gateway):
        """Path A with B's uuid is refused by the route; it must not use
        up B's budget, which B's buyer then still has."""
        one, two = OrderFactory(user=None), OrderFactory(user=None)

        # The route refuses these; the address is what they are charged to.
        assert _pay(gateway, one, two.uuid) == 403
        assert _pay(gateway, two) != 429
