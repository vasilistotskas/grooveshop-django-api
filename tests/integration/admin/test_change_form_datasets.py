"""Datasets page a change form's related rows instead of rendering
them all, and show only the rows of the object being edited."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib import admin
from django.test import Client, RequestFactory
from django.urls import reverse

from admin.datasets import RelatedDatasetAdmin
from giftcard.enum import GiftCardTransactionKind
from giftcard.factories import GiftCardFactory
from giftcard.models import GiftCardTransaction
from order.factories import OrderFactory
from order.models.stock_log import StockLog
from product.admin import StockLogDatasetAdmin
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    client = Client()
    client.force_login(
        UserAccountFactory(admin=True),
        backend="tenant.auth_backends.PlatformStaffBackend",
    )
    return client


def _log(product, n):
    return StockLog.objects.create(
        product=product,
        operation_type=StockLog.OPERATION_INCREMENT,
        quantity_delta=1,
        stock_before=n,
        stock_after=n + 1,
    )


def test_stock_activity_is_paged(client):
    product = ProductFactory(num_images=0, num_reviews=0)
    for n in range(25):
        _log(product, n)
    _log(ProductFactory(num_images=0, num_reviews=0), 999)

    response = client.get(
        reverse("admin:product_product_change", args=[product.pk])
    )

    assert response.status_code == 200
    dataset = next(
        d for d in response.context["datasets"] if d.model is StockLog
    )
    changelist = dataset.cl
    assert changelist.result_count == 25
    assert len(changelist.result_list) == 20
    assert all(log.product_id == product.pk for log in changelist.result_list)


def test_a_customer_sees_only_their_orders(client):
    customer = UserAccountFactory()
    mine = OrderFactory(user=customer, num_order_items=0)
    OrderFactory(user=UserAccountFactory(), num_order_items=0)

    response = client.get(
        reverse("admin:user_useraccount_change", args=[customer.pk])
    )

    assert response.status_code == 200
    (dataset,) = response.context["datasets"]
    assert [order.pk for order in dataset.cl.result_list] == [mine.pk]


def test_a_gift_card_shows_its_own_ledger(client):
    card = GiftCardFactory()
    GiftCardTransaction.objects.create(
        gift_card=card, kind=GiftCardTransactionKind.ADJUST, amount=Decimal(5)
    )
    other = GiftCardFactory()
    GiftCardTransaction.objects.create(
        gift_card=other, kind=GiftCardTransactionKind.ADJUST, amount=Decimal(7)
    )

    response = client.get(
        reverse("admin:giftcard_giftcard_change", args=[card.pk])
    )

    assert response.status_code == 200
    (dataset,) = response.context["datasets"]
    assert {row.gift_card_id for row in dataset.cl.result_list} == {card.pk}


def test_datasets_offer_no_bulk_actions(client):
    """A superuser may delete stock logs on their own changelist; from a
    product page they are read-only history, so no "delete selected"."""
    product = ProductFactory(num_images=0, num_reviews=0)
    _log(product, 0)

    response = client.get(
        reverse("admin:product_product_change", args=[product.pk])
    )

    dataset = next(
        d for d in response.context["datasets"] if d.model is StockLog
    )
    assert dataset.cl.model_admin.get_actions(response.wsgi_request) == {}
    assert not any(
        subclass.actions for subclass in RelatedDatasetAdmin.__subclasses__()
    )


def test_no_view_permission_means_no_rows():
    product = ProductFactory(num_images=0, num_reviews=0)
    _log(product, 0)
    request = RequestFactory().get("/")
    request.user = UserAccountFactory()  # no permissions at all
    dataset_admin = StockLogDatasetAdmin(StockLog, admin.site)
    dataset_admin.extra_context = {"object": str(product.pk)}

    assert not dataset_admin.get_queryset(request).exists()


def test_the_order_page_opens_with_its_summary(client):
    from order.enum.status import OrderStatus

    customer = UserAccountFactory()
    order = OrderFactory(user=customer, status=OrderStatus.PROCESSING)

    response = client.get(reverse("admin:order_order_change", args=[order.pk]))

    assert response.status_code == 200
    titles = [str(card["title"]) for card in response.context["order_summary"]]
    assert len(titles) == 6
    html = response.content.decode()
    assert order.get_status_display() in html
    assert f"/user/useraccount/{customer.pk}/change/" in html
