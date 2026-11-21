"""The payment budget of an agent gateway call, through the real route.

Every payment route authorizes the order its `pk` names and checks that
`?uuid=` opens it (`OrderViewSet.check_object_permissions`). The
throttle reads the same two sources (`parser_context` kwargs and the
query), so a call budgets the order the route authorizes: one order's
calls cannot spend another's, nor can a uuid that opens nothing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from cart.agent_token import mint_cart_token
from cart.models import Cart
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.throttles import PaymentAttemptAnonThrottle, PaymentAttemptThrottle
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from tests.utils.fake_stripe import fake_stripe
from tests.utils.redis import private_cache

SECRET = "gateway-shared-secret"


@pytest.fixture(autouse=True)
def _no_provider():
    """The route that is not throttled goes on to start a payment: the
    provider is in memory (as in the payment-intent tests), so the result
    never depends on credentials in the environment. A call the budget
    lets through is a payment started (200), so an order the route refuses
    for another reason cannot pass for one the budget allowed."""
    with fake_stripe():
        yield


@pytest.fixture
def gateway(monkeypatch, settings) -> APIClient:
    settings.AGENT_GATEWAY_INTERNAL_SECRET = SECRET
    for throttle in (PaymentAttemptThrottle, PaymentAttemptAnonThrottle):
        monkeypatch.setattr(throttle, "rate", "1/minute", raising=False)
        monkeypatch.setattr(throttle, "cache", private_cache(), raising=False)
    client = APIClient()
    client.credentials(HTTP_X_INTERNAL_GATEWAY=SECRET)
    return client


def _order():
    """An order the route goes on to start a payment for: the factory's
    random status, pay way or earlier payment (a ``payment_id`` the route
    refuses to replace while it is in progress) would make that a matter
    of luck."""
    return OrderFactory(
        user=None,
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        payment_id="",
        pay_way=PayWayFactory(
            provider_code="stripe",
            settlement=PaySettlement.ONLINE,
            active=True,
        ),
    )


def _pay(client: APIClient, order, uuid_value=None, **headers) -> int:
    url = reverse("order-create-payment-intent", kwargs={"pk": order.pk})
    return client.post(
        f"{url}?uuid={uuid_value or order.uuid}", format="json", headers=headers
    ).status_code


def _guest_cart_id() -> str:
    return str(Cart.objects.create(user=None).uuid)


def _live_cart_token() -> str:
    """A token as the mint stores it; the budget reads only the grant."""
    cart = Cart.objects.create(user=None)
    return mint_cart_token(cart, SimpleNamespace(user_id=1, digest="d"))


@pytest.mark.django_db
class TestPaymentBudgetFollowsTheRoute:
    def test_an_order_has_its_own_budget(self, gateway):
        one, two = _order(), _order()

        assert _pay(gateway, one) == 200
        assert _pay(gateway, one) == 429
        assert _pay(gateway, two) == 200

    def test_another_orders_uuid_spends_nothing_of_it(self, gateway):
        """Path A with B's uuid is refused by the route; it must not use
        up B's budget, which B's buyer then still has."""
        one, two = _order(), _order()

        # The route refuses these; the address is what they are charged to.
        assert _pay(gateway, one, two.uuid) == 403
        assert _pay(gateway, two) == 200

    @pytest.mark.parametrize(
        ("header", "new_cart"),
        [("X-Cart-Id", _guest_cart_id), ("X-Cart-Token", _live_cart_token)],
    )
    def test_another_cart_header_buys_no_fresh_budget(
        self, gateway, header, new_cart
    ):
        """A payment route acts on its order: the cart a call names is not
        read, so sending another one spends the same order's budget."""
        order = _order()

        assert _pay(gateway, order, **{header: new_cart()}) == 200
        assert _pay(gateway, order, **{header: new_cart()}) == 429
