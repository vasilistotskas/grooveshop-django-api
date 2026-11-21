"""Every task that notifies in the app does it once per dispatch.

A task queued through ``dispatch_on_commit`` can run more than once: the
message a failed publish wrote and the sweep's copy of it, a message
delivered again after a worker was lost, a retry. Every copy carries the
task id the helper chose, and each task here writes its notifications only
through ``create_user_notification``, which keys the row on that id. Each
task is run twice under one id (the second copy writes nothing) and once
more under another id: a new dispatch writes again, so the first two runs
prove the id, not a guard of the task's own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest
from django.core import mail

from blog.tasks import notify_comment_liked_task
from loyalty.tasks import notify_loyalty_tier_up_live
from notification.models.user import NotificationUser
from order.communications.notifications import (
    notify_order_created_live,
    notify_order_refunded_live,
    notify_order_status_changed_live,
    notify_payment_confirmed_live,
    notify_payment_failed_live,
)
from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from product.factories import ProductFavouriteFactory
from product.factories.product import ProductFactory
from product.tasks import (
    notify_back_in_stock_favourites_live,
    send_price_drop_notifications,
)
from shipping.models import ShippingProvider
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.tasks import acs_send_arrival_notification
from shipping_boxnow.factories import (
    BoxNowParcelEventFactory,
    BoxNowShipmentFactory,
)
from shipping_boxnow.tasks import boxnow_send_arrival_notification
from tests.utils.emails import open_email_gates  # noqa: F401
from user.factories.account import UserAccountFactory

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("open_email_gates"),
]


@pytest.fixture(autouse=True)
def _storefront(settings, bind_tenant):
    settings.NUXT_BASE_URL = "https://shop.example"
    bind_tenant(None)
    mail.outbox = []


@pytest.fixture(autouse=True)
def _favourites_on():
    with patch("tenant.features.feature_on", return_value=True):
        yield


@dataclass(frozen=True)
class Dispatch:
    task: Any
    args: list[Any]


def _customer_order():
    return OrderFactory(
        user=UserAccountFactory(num_addresses=0), num_order_items=0
    )


def _order_created() -> Dispatch:
    return Dispatch(notify_order_created_live, [_customer_order().id])


def _order_shipped() -> Dispatch:
    return Dispatch(
        notify_order_status_changed_live,
        [_customer_order().id, OrderStatus.SHIPPED.value],
    )


def _payment_confirmed() -> Dispatch:
    return Dispatch(notify_payment_confirmed_live, [_customer_order().id])


def _payment_failed() -> Dispatch:
    return Dispatch(notify_payment_failed_live, [_customer_order().id])


def _order_refunded() -> Dispatch:
    return Dispatch(notify_order_refunded_live, [_customer_order().id])


def _loyalty_tier_up() -> Dispatch:
    user = UserAccountFactory(num_addresses=0)
    return Dispatch(notify_loyalty_tier_up_live, [user.id])


def _comment_liked() -> Dispatch:
    owner = UserAccountFactory(num_addresses=0)
    likers = [UserAccountFactory(num_addresses=0) for _ in range(2)]
    return Dispatch(
        notify_comment_liked_task,
        [1, [liker.id for liker in likers], "/blog/post/1/slug", owner.id],
    )


def _favourited_product():
    product = ProductFactory(stock=5, num_images=0, num_reviews=0)
    for _ in range(2):
        ProductFavouriteFactory(
            product=product, user=UserAccountFactory(num_addresses=0)
        )
    return product


def _back_in_stock() -> Dispatch:
    return Dispatch(
        notify_back_in_stock_favourites_live, [_favourited_product().id]
    )


def _price_drop() -> Dispatch:
    return Dispatch(
        send_price_drop_notifications, [_favourited_product().id, 20.0, 10.0]
    )


def _acs_out_for_delivery() -> Dispatch:
    ShippingProvider.objects.filter(code="acs").update(is_active=True)
    shipment = AcsShipmentFactory(order=_customer_order(), with_voucher=True)
    return Dispatch(acs_send_arrival_notification, [shipment.id])


def _boxnow_at_locker() -> Dispatch:
    ShippingProvider.objects.filter(code="boxnow").update(is_active=True)
    event = BoxNowParcelEventFactory(
        shipment=BoxNowShipmentFactory(
            order=_customer_order(), with_parcel=True
        )
    )
    return Dispatch(boxnow_send_arrival_notification, [event.id])


DISPATCHES: dict[str, Callable[[], Dispatch]] = {
    "order created": _order_created,
    "order shipped": _order_shipped,
    "payment confirmed": _payment_confirmed,
    "payment failed": _payment_failed,
    "order refunded": _order_refunded,
    "loyalty tier up": _loyalty_tier_up,
    "comment liked": _comment_liked,
    "back in stock": _back_in_stock,
    "price drop": _price_drop,
    "acs out for delivery": _acs_out_for_delivery,
    "boxnow at locker": _boxnow_at_locker,
}


@pytest.mark.parametrize("build", DISPATCHES.values(), ids=DISPATCHES.keys())
def test_a_second_copy_of_a_dispatch_notifies_nobody(build):
    dispatch = build()

    def run(task_id: str) -> None:
        dispatch.task.apply(dispatch.args, task_id=task_id, throw=True)

    # Building an order notifies too (its own ``order_created`` dispatch).
    before = NotificationUser.objects.count()

    def written() -> int:
        return NotificationUser.objects.count() - before

    run("one-dispatch")
    first = written()
    assert first, "the task notified nobody: the case proves nothing"

    run("one-dispatch")
    assert written() == first

    run("another-dispatch")
    assert written() == 2 * first
