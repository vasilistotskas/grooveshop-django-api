"""The "Order email" slot: suggestions in the order confirmation, seeded
with what was bought, recorded as an impression when the email is sent
so the nightly weight learning measures this surface like the others.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.db.models.signals import post_save
from factory.django import mute_signals

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.tasks import send_order_confirmation_email
from product.factories.product import ProductFactory
from product.models import ProductRelation
from recommendation.enum import EventKind, StrategyCode, Surface
from recommendation.models import RecommendationEvent, RecommendationSlot
from tests.utils.store_settings import switch_store_settings

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _store_shows_suggestions():
    switch_store_settings(PRODUCT_SUGGESTIONS_ENABLED=True)


@pytest.fixture(autouse=True)
def _slot():
    return RecommendationSlot.objects.create(
        surface=Surface.ORDER_EMAIL,
        strategy_chain=[StrategyCode.CURATED],
        weights={},
        limit=3,
        min_fill=1,
    )


def _product():
    return ProductFactory(active=True, stock=5, num_images=0, num_reviews=0)


def _order_of(product):
    # Muted: ``order_created`` would send this confirmation eagerly
    # before the test sends it.
    with mute_signals(post_save):
        order = OrderFactory(
            num_order_items=0,
            email="shopper@example.com",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        OrderItemFactory(order=order, product=product)
    return order


def _send(order) -> tuple[str, str]:
    with patch("emails.transport.EmailMultiAlternatives") as email:
        instance = MagicMock()
        email.return_value = instance
        assert send_order_confirmation_email(order.id) is True
    instance.send.assert_called_once()
    text = email.call_args.kwargs["body"]
    html = instance.attach_alternative.call_args.args[0]
    return text, html


@pytest.fixture
def bought_and_paired():
    bought, paired = _product(), _product()
    ProductRelation.objects.create(from_product=bought, to_product=paired)
    return bought, paired


def test_the_confirmation_suggests_what_goes_with_the_order(bought_and_paired):
    bought, paired = bought_and_paired
    name = paired.safe_translation_getter("name", any_language=True)

    text, html = _send(_order_of(bought))

    for body in (text, html):
        assert name in body
        assert paired.storefront_path in body
        # What was bought is never suggested back.
        assert bought.storefront_path not in body


def test_the_sent_strip_is_recorded_as_an_impression(bought_and_paired):
    bought, paired = bought_and_paired
    order = _order_of(bought)

    _send(order)

    event = RecommendationEvent.objects.get()
    assert event.kind == EventKind.IMPRESSION
    assert event.surface == Surface.ORDER_EMAIL
    assert event.strategy == StrategyCode.CURATED
    assert event.product_id == paired.id
    assert event.user_id == order.user_id


def test_no_strip_while_the_store_shows_no_suggestions(bought_and_paired):
    switch_store_settings(PRODUCT_SUGGESTIONS_ENABLED=False)
    bought, paired = bought_and_paired

    text, html = _send(_order_of(bought))

    assert paired.storefront_path not in text
    assert paired.storefront_path not in html
    assert not RecommendationEvent.objects.exists()


def test_nothing_worth_suggesting_leaves_the_email_as_it_was():
    bought = _product()

    text, html = _send(_order_of(bought))

    assert "/products/" not in text
    assert "/products/" not in html
    assert not RecommendationEvent.objects.exists()


def test_a_broken_strip_never_costs_the_confirmation(bought_and_paired):
    bought, paired = bought_and_paired

    with patch(
        "order.emails.order_email_strip", side_effect=RuntimeError("boom")
    ):
        text, html = _send(_order_of(bought))

    assert paired.storefront_path not in text
    assert paired.storefront_path not in html
    assert not RecommendationEvent.objects.exists()
