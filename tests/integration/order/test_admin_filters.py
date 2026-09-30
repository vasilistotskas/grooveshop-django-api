"""The order changelist's status filters select several values at once.

They replaced hand-written "group" filters (active, problematic, needs
attention) that duplicated the plain status filters beside them; a
group is now several statuses picked together.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    user = User.objects.create_superuser(
        username="boss", email="boss@example.com", password="x"
    )
    client = Client()
    client.force_login(
        user, backend="tenant.auth_backends.PlatformStaffBackend"
    )
    return client


def _listed(client, query: str) -> set[int]:
    response = client.get(f"{reverse('admin:order_order_changelist')}?{query}")
    assert response.status_code == 200
    return {order.pk for order in response.context["cl"].result_list}


def test_several_statuses_are_ored(client):
    pending = OrderFactory(status=OrderStatus.PENDING, num_order_items=0)
    processing = OrderFactory(status=OrderStatus.PROCESSING, num_order_items=0)
    OrderFactory(status=OrderStatus.COMPLETED, num_order_items=0)

    assert _listed(
        client, "status__exact=PENDING&status__exact=PROCESSING"
    ) == {pending.pk, processing.pk}


def test_several_payment_statuses_are_ored(client):
    failed = OrderFactory(
        payment_status=PaymentStatus.FAILED, num_order_items=0
    )
    pending = OrderFactory(
        payment_status=PaymentStatus.PENDING, num_order_items=0
    )
    OrderFactory(payment_status=PaymentStatus.COMPLETED, num_order_items=0)

    assert _listed(
        client, "payment_status__exact=FAILED&payment_status__exact=PENDING"
    ) == {failed.pk, pending.pk}
