"""Every mail task queued after a commit mails once per dispatch.

A task queued through ``dispatch_on_commit`` can run more than once: the
message a failed publish wrote and the sweep's copy of it, a message
delivered again after a worker was lost, a retry. Every copy carries the
task id the helper chose, and each task here mails only through
``send_email``, whose delivery is keyed on that id (``emails.log``). Each
task is run twice under one id (the second copy mails nobody) and once more
under another id: a new dispatch mails again, so the first two runs prove
the id, not a guard of the task's own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core import mail

from b2b.factories import BusinessProfileFactory
from b2b.tasks import (
    send_admin_new_business_profile_email,
    send_business_profile_status_email,
)
from contact.factories import ContactFactory, FeedbackFactory
from contact.tasks import (
    send_contact_notification_email_task,
    send_feedback_notification_email_task,
)
from giftcard.enum import GiftCardPurchaseStatus
from giftcard.factories import GiftCardPurchaseFactory
from giftcard.tasks import send_gift_card_purchase_receipt
from order.factories.order import OrderFactory
from order.tasks import (
    send_admin_new_order_email,
    send_dispute_notification_email,
)
from product.alerts import request_guest_alert
from product.enum.review import ReviewStatus
from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory
from product.models.alert import ProductAlertKind
from product.tasks import (
    send_product_alert_confirmation,
    send_review_pending_alert,
)
from shipping.models import ShippingProvider
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.tasks import acs_send_arrival_notification
from shipping_boxnow.factories import (
    BoxNowParcelEventFactory,
    BoxNowShipmentFactory,
)
from shipping_boxnow.tasks import boxnow_send_arrival_notification
from tenant.tasks import send_staff_password_reset_email
from tests.utils.staff import store_staff
from user.emails import ALLAUTH_TYPES, AllauthMail
from user.factories.account import UserAccountFactory
from user.factories.subscription import UserSubscriptionFactory
from user.models.data_export import UserDataExport
from user.models.subscription import UserSubscription
from user.tasks import (
    export_user_data_task,
    send_allauth_email_task,
    send_newsletter_already_subscribed_email_task,
    send_subscription_confirmation_email_task,
)

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("tenant", "open_email_gates"),
]

OPERATOR = "owner@store.test"


@pytest.fixture(autouse=True)
def _operators():
    mail.outbox.clear()
    with patch(
        "emails.recipients.store_operator_addresses", return_value=[OPERATOR]
    ):
        yield


@dataclass(frozen=True)
class Dispatch:
    task: Any
    args: list[Any]
    kwargs: dict[str, Any]


def _order_id() -> int:
    return OrderFactory(num_order_items=0).id


def _admin_new_order(tenant) -> Dispatch:
    return Dispatch(send_admin_new_order_email, [_order_id()], {})


def _dispute(tenant) -> Dispatch:
    return Dispatch(
        send_dispute_notification_email, [_order_id(), "dp_720", "stripe"], {}
    )


def _gift_card_receipt(tenant) -> Dispatch:
    purchase = GiftCardPurchaseFactory(status=GiftCardPurchaseStatus.PAID)
    return Dispatch(send_gift_card_purchase_receipt, [purchase.id], {})


def _business_profile_reviewed(tenant) -> Dispatch:
    profile = BusinessProfileFactory(approved=True)
    return Dispatch(send_business_profile_status_email, [profile.id], {})


def _business_profile_received(tenant) -> Dispatch:
    profile = BusinessProfileFactory()
    return Dispatch(send_admin_new_business_profile_email, [profile.id], {})


def _contact(tenant) -> Dispatch:
    return Dispatch(
        send_contact_notification_email_task, [ContactFactory().id], {}
    )


def _feedback(tenant) -> Dispatch:
    return Dispatch(
        send_feedback_notification_email_task, [FeedbackFactory().id], {}
    )


def _staff_password_reset(tenant) -> Dispatch:
    staff = store_staff(tenant)
    return Dispatch(
        send_staff_password_reset_email,
        [],
        {
            "email": staff.email,
            "host": "shop.example",
            "secure": True,
            "site_name": "admin",
            "urlconf": settings.ROOT_URLCONF,
        },
    )


def _allauth(tenant) -> Dispatch:
    mail_ = AllauthMail.from_allauth(
        address="visitor@example.test",
        language="en",
        context={"signup_url": "https://shop.example/account/signup"},
    )
    return Dispatch(
        send_allauth_email_task,
        [],
        mail_.task_kwargs(ALLAUTH_TYPES["account/email/unknown_account"]),
    )


def _data_export(tenant) -> Dispatch:
    user = UserAccountFactory(num_addresses=0)
    export = UserDataExport.objects.create(user=user)
    return Dispatch(export_user_data_task, [export.id], {})


def _alert_confirmation(tenant) -> Dispatch:
    product = ProductFactory(stock=0, num_images=0, num_reviews=0)
    alert = request_guest_alert(
        product_id=product.id,
        kind=ProductAlertKind.RESTOCK,
        email="guest@example.test",
        target_price=None,
        target_price_currency="EUR",
        language="en",
    )
    assert alert is not None
    return Dispatch(send_product_alert_confirmation, [alert.pk], {})


def _review_pending(tenant) -> Dispatch:
    review = ProductReviewFactory(status=ReviewStatus.NEW)
    return Dispatch(send_review_pending_alert, [review.pk], {})


def _subscription_confirmation(tenant) -> Dispatch:
    subscription = UserSubscriptionFactory(
        status=UserSubscription.SubscriptionStatus.PENDING,
        confirmation_token="tok-720",
    )
    return Dispatch(
        send_subscription_confirmation_email_task, [subscription.id], {}
    )


def _already_subscribed(tenant) -> Dispatch:
    subscription = UserSubscriptionFactory(
        status=UserSubscription.SubscriptionStatus.ACTIVE
    )
    return Dispatch(
        send_newsletter_already_subscribed_email_task, [subscription.id], {}
    )


def _acs_out_for_delivery(tenant) -> Dispatch:
    ShippingProvider.objects.filter(code="acs").update(is_active=True)
    shipment = AcsShipmentFactory(
        order=OrderFactory(num_order_items=0), with_voucher=True
    )
    return Dispatch(acs_send_arrival_notification, [shipment.id], {})


def _boxnow_at_locker(tenant) -> Dispatch:
    ShippingProvider.objects.filter(code="boxnow").update(is_active=True)
    event = BoxNowParcelEventFactory(
        shipment=BoxNowShipmentFactory(
            order=OrderFactory(num_order_items=0), with_parcel=True
        )
    )
    return Dispatch(boxnow_send_arrival_notification, [event.id], {})


DISPATCHES: dict[str, Callable[[Any], Dispatch]] = {
    "merchant new order": _admin_new_order,
    "dispute": _dispute,
    "gift card receipt": _gift_card_receipt,
    "business profile reviewed": _business_profile_reviewed,
    "business profile received": _business_profile_received,
    "contact": _contact,
    "feedback": _feedback,
    "staff password reset": _staff_password_reset,
    "allauth": _allauth,
    "data export": _data_export,
    "alert confirmation": _alert_confirmation,
    "review pending": _review_pending,
    "subscription confirmation": _subscription_confirmation,
    "already subscribed": _already_subscribed,
    "acs out for delivery": _acs_out_for_delivery,
    "boxnow at locker": _boxnow_at_locker,
}


@pytest.fixture(autouse=True)
def _private_media(settings, tmp_path):
    """The data export writes its bundle under private media."""
    settings.PRIVATE_MEDIA_ROOT = str(tmp_path)


@pytest.mark.parametrize("build", DISPATCHES.values(), ids=DISPATCHES.keys())
def test_a_second_copy_of_a_dispatch_mails_nobody(build, tenant):
    dispatch = build(tenant)

    def run(task_id: str) -> None:
        dispatch.task.apply(
            dispatch.args, dispatch.kwargs, task_id=task_id, throw=True
        )

    run("one-dispatch")
    first = len(mail.outbox)
    assert first, "the task mailed nobody: the case proves nothing"

    run("one-dispatch")
    assert len(mail.outbox) == first

    run("another-dispatch")
    assert len(mail.outbox) == 2 * first
