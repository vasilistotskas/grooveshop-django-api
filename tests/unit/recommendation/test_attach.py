"""``attach``: an order line tied back to the impression that showed it.

Two windows, both recorded and labelled: the same cart within
``RECOMMENDATION_ATTACH_CART_WINDOW_HOURS`` and the same signed-in
customer within ``RECOMMENDATION_ATTACH_USER_WINDOW_DAYS``. Everything
here pins the boundaries, the precedence, and the idempotence a
retried Celery task relies on.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from product.factories.product import ProductFactory
from recommendation.enum import AttachMatch, EventKind, StrategyCode, Surface
from recommendation.events import record_attach_events
from recommendation.models import RecommendationEvent
from recommendation.tasks import record_recommendation_attach
from user.factories.account import UserAccountFactory

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("_windows"),
]

CART = uuid.uuid4()
OTHER_CART = uuid.uuid4()


@pytest.fixture
def _windows():
    with override_settings(
        RECOMMENDATION_ATTACH_CART_WINDOW_HOURS=24,
        RECOMMENDATION_ATTACH_USER_WINDOW_DAYS=7,
    ):
        yield


@pytest.fixture
def product():
    return ProductFactory(num_images=0, num_reviews=0)


@pytest.fixture
def user():
    return UserAccountFactory(num_addresses=0)


def _order(product, *, user=None, cart_uuid=CART):
    order = OrderFactory(user=user, num_order_items=0)
    if cart_uuid is not None:
        order.metadata = {"cart_snapshot": {"cart_uuid": str(cart_uuid)}}
        order.save(update_fields=["metadata"])
    OrderItemFactory(order=order, product=product, quantity=1)
    return order


def _impression(product, *, age: timedelta, cart_uuid=CART, user=None, **kw):
    kw.setdefault("surface", Surface.PDP)
    kw.setdefault("strategy", StrategyCode.CURATED)
    kw.setdefault("position", 0)
    kw.setdefault("impression_id", uuid.uuid4())
    row = RecommendationEvent.objects.create(
        kind=EventKind.IMPRESSION,
        product=product,
        cart_uuid=cart_uuid,
        user=user,
        **kw,
    )
    # auto_now_add: backdate after the insert.
    RecommendationEvent.objects.filter(pk=row.pk).update(
        created_at=timezone.now() - age
    )
    row.refresh_from_db()
    return row


def _attaches(order):
    return RecommendationEvent.objects.filter(
        kind=EventKind.ATTACH, order=order
    )


def test_same_cart_inside_the_window_attaches_with_the_impression_facts(
    product,
):
    shown = _impression(
        product, age=timedelta(hours=2), surface=Surface.CART, position=3
    )
    order = _order(product)

    assert record_attach_events(order.id) == 1
    (row,) = _attaches(order)
    assert row.matched_by == AttachMatch.CART
    assert row.surface == Surface.CART
    assert row.strategy == StrategyCode.CURATED
    assert row.position == 3
    assert row.impression_id == shown.impression_id
    assert row.product_id == product.id
    assert row.cart_uuid == CART


def test_a_product_shown_but_not_bought_does_not_attach(product):
    other = ProductFactory(num_images=0, num_reviews=0)
    _impression(other, age=timedelta(hours=1))
    order = _order(product)

    assert record_attach_events(order.id) == 0


def test_same_cart_outside_the_window_does_not_attach(product):
    _impression(product, age=timedelta(hours=25))
    order = _order(product)

    assert record_attach_events(order.id) == 0


def test_same_customer_inside_the_window_attaches_as_user(product, user):
    _impression(product, age=timedelta(days=6), cart_uuid=OTHER_CART, user=user)
    order = _order(product, user=user)

    assert record_attach_events(order.id) == 1
    assert _attaches(order).get().matched_by == AttachMatch.USER


def test_same_customer_outside_the_window_does_not_attach(product, user):
    _impression(product, age=timedelta(days=8), cart_uuid=OTHER_CART, user=user)
    order = _order(product, user=user)

    assert record_attach_events(order.id) == 0


def test_the_cart_window_takes_precedence_when_both_match(product, user):
    _impression(product, age=timedelta(hours=1), cart_uuid=CART, user=user)
    order = _order(product, user=user)

    assert record_attach_events(order.id) == 1
    assert _attaches(order).get().matched_by == AttachMatch.CART


def test_an_impression_after_the_order_does_not_attach(product):
    order = _order(product)
    _impression(product, age=timedelta(seconds=-60))

    assert record_attach_events(order.id) == 0


def test_a_retry_writes_nothing_twice(product):
    _impression(product, age=timedelta(hours=1))
    order = _order(product)

    assert record_attach_events(order.id) == 1
    assert record_attach_events(order.id) == 1
    assert _attaches(order).count() == 1


def test_one_attach_per_impression_even_if_shown_twice(product):
    shown = _impression(product, age=timedelta(hours=1))
    _impression(
        product, age=timedelta(hours=2), impression_id=shown.impression_id
    )
    _impression(product, age=timedelta(hours=3))
    order = _order(product)

    assert record_attach_events(order.id) == 2


def test_an_order_with_no_cart_and_no_customer_attaches_nothing(product):
    _impression(product, age=timedelta(hours=1))
    order = _order(product, cart_uuid=None)

    assert record_attach_events(order.id) == 0


def _carrying(order, product, impression_id):
    """The storefront carried this impression onto the line at
    add-to-cart; the order line inherited it at checkout."""
    order.items.filter(product=product).update(
        recommendation_impression_id=impression_id
    )


def test_a_carried_impression_attaches_a_guest_with_no_identity_at_all(
    product,
):
    """The first-visit journey: the strip was shown before any cart
    existed (no cart_uuid on the impression), the shopper is a guest,
    and the order has no snapshot either. Only the carried id can tie
    the line back — and it does."""
    shown = _impression(product, age=timedelta(hours=2), cart_uuid=None)
    order = _order(product, cart_uuid=None)
    _carrying(order, product, shown.impression_id)

    assert record_attach_events(order.id) == 1
    row = _attaches(order).get()
    assert row.matched_by == AttachMatch.IMPRESSION
    assert row.impression_id == shown.impression_id


def test_a_carried_impression_is_not_bound_by_the_windows(product):
    shown = _impression(product, age=timedelta(days=30), cart_uuid=None)
    order = _order(product, cart_uuid=None)
    _carrying(order, product, shown.impression_id)

    assert record_attach_events(order.id) == 1


def test_a_carried_impression_takes_precedence_over_the_cart_window(
    product,
):
    shown = _impression(product, age=timedelta(hours=1), cart_uuid=CART)
    order = _order(product)
    _carrying(order, product, shown.impression_id)

    assert record_attach_events(order.id) == 1
    assert _attaches(order).get().matched_by == AttachMatch.IMPRESSION


def test_a_carried_impression_attaches_only_the_line_that_carries_it(
    product,
):
    """One impression id covers the whole strip, so the same id also
    sits on rows for the strip's other products. Buying one of those
    without carrying its id is not an attach on this identity — and
    must never be written under a window the order never matched."""
    also_shown = ProductFactory(num_images=0, num_reviews=0)
    impression_id = uuid.uuid4()
    shown = _impression(
        product,
        age=timedelta(hours=2),
        cart_uuid=None,
        impression_id=impression_id,
        position=0,
    )
    _impression(
        also_shown,
        age=timedelta(hours=2),
        cart_uuid=None,
        impression_id=impression_id,
        position=1,
    )
    order = _order(product, cart_uuid=None)
    OrderItemFactory(order=order, product=also_shown, quantity=1)
    _carrying(order, product, shown.impression_id)

    assert record_attach_events(order.id) == 1
    (row,) = _attaches(order)
    assert row.product_id == product.id
    assert row.matched_by == AttachMatch.IMPRESSION


def test_a_carried_impression_for_another_product_attaches_nothing(
    product,
):
    other = ProductFactory(num_images=0, num_reviews=0)
    shown = _impression(other, age=timedelta(hours=1), cart_uuid=None)
    order = _order(product, cart_uuid=None)
    _carrying(order, product, shown.impression_id)

    assert record_attach_events(order.id) == 0


def test_the_task_returns_the_count(product):
    _impression(product, age=timedelta(hours=1))
    order = _order(product)

    assert record_recommendation_attach.apply(
        kwargs={"order_id": order.id}
    ).get() == {
        "order_id": order.id,
        "rows": 1,
    }
