"""The reminder to review what was delivered: which orders, once each.

One email per order, ``REVIEW_REQUEST_DELAY_DAYS`` days after the order's
first move to DELIVERED (the ``OrderHistory`` row), only for products the
customer has not reviewed, never twice.
"""

from __future__ import annotations

import smtplib
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core import mail
from django.utils import timezone
from extra_settings.models import Setting

from emails import optout
from emails.models import EVERY_MARKETING_TYPE, EmailOptOut, EmailSwitch
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.models import Order, OrderHistory
from order.review_request import (
    MAX_DELAY_DAYS,
    delivery_window,
    validate_review_request_delay_days_setting,
)
from order.tasks import send_review_request_emails
from product.enum.review import ReviewStatus
from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)
from tests.utils.store_settings import switch_store_settings
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

DELAY = 8
CATCH_UP = settings.REVIEW_REQUEST_CATCH_UP_DAYS


@pytest.fixture(autouse=True)
def store():
    tenant = store_tenant("review_store", default_locale="en")
    previous = bind_store_tenant(tenant)
    Setting.objects.update_or_create(
        name="REVIEW_REQUEST_DELAY_DAYS",
        defaults={"value_type": Setting.TYPE_INT, "value_int": DELAY},
    )
    yield tenant
    unbind_store_tenant(previous)


def test_a_store_sends_no_reminder_until_it_chooses_to():
    # Mail to a store's customers never starts by itself on a deploy.
    declared = {
        entry["name"]: entry["value"]
        for entry in settings.EXTRA_SETTINGS_DEFAULTS
    }
    assert declared["REVIEW_REQUEST_DELAY_DAYS"] == 0


def set_delay(days: int) -> None:
    Setting.objects.update_or_create(
        name="REVIEW_REQUEST_DELAY_DAYS",
        defaults={"value_type": Setting.TYPE_INT, "value_int": days},
    )


@pytest.fixture
def customer():
    return UserAccountFactory(num_addresses=0, language_code="en")


def product(name: str):
    created = ProductFactory(num_images=0, num_reviews=0, slug=name)
    created.set_current_language("en")
    created.name = name.replace("-", " ").title()
    created.save()
    return created


def delivered(
    customer,
    products,
    *,
    days_ago: float = DELAY + 0.5,
    status: str = OrderStatus.DELIVERED,
) -> Order:
    """An order its customer received *days_ago* days ago.

    Built in two steps, like the golden cases: creating an order fires its
    own emails, so the state the case needs is set with ``update()``
    afterwards, which signals nothing."""
    order = OrderFactory(
        num_order_items=0,
        user=customer,
        email=customer.email,
        language_code="en",
        first_name="Maria",
    )
    for item in products:
        OrderItemFactory(order=order, product=item, quantity=1)
    Order.objects.filter(pk=order.pk).update(
        status=status, payment_status=PaymentStatus.COMPLETED
    )
    OrderHistory.log_status_change(
        order, OrderStatus.SHIPPED, OrderStatus.DELIVERED
    )
    OrderHistory.objects.filter(
        order=order, change_type="STATUS", new_value__status="DELIVERED"
    ).update(created_at=timezone.now() - timedelta(days=days_ago))
    mail.outbox.clear()
    return Order.objects.get(pk=order.pk)


def stamped(order: Order) -> bool:
    return Order.objects.get(pk=order.pk).review_request_sent_at is not None


class TestWhichOrders:
    def test_an_order_delivered_the_delay_ago_is_reminded(self, customer):
        order = delivered(customer, [product("alpha")])

        result = send_review_request_emails.run()

        assert result == {"sent": 1, "skipped": 0, "failed": 0}
        [message] = mail.outbox
        assert message.to == [order.email]
        assert stamped(order)

    def test_an_order_not_yet_due_is_left_alone(self, customer):
        order = delivered(customer, [product("alpha")], days_ago=DELAY - 1)

        send_review_request_emails.run()

        assert mail.outbox == []
        assert not stamped(order)

    def test_an_order_past_the_catch_up_is_left_alone(self, customer):
        order = delivered(
            customer, [product("alpha")], days_ago=DELAY + CATCH_UP + 1
        )

        send_review_request_emails.run()

        assert mail.outbox == []
        assert not stamped(order)

    def test_an_order_in_the_catch_up_is_reminded(self, customer):
        delivered(customer, [product("alpha")], days_ago=DELAY + CATCH_UP - 1)

        assert send_review_request_emails.run()["sent"] == 1

    @pytest.mark.parametrize(
        "status",
        [OrderStatus.RETURNED, OrderStatus.REFUNDED, OrderStatus.CANCELED],
    )
    def test_an_order_that_did_not_stay_delivered_is_left_alone(
        self, customer, status
    ):
        delivered(customer, [product("alpha")], status=status)

        send_review_request_emails.run()

        assert mail.outbox == []

    def test_a_completed_order_counts_from_its_delivery(self, customer):
        order = delivered(
            customer, [product("alpha")], status=OrderStatus.COMPLETED
        )
        # Completing moved ``status_updated_at`` to today, as it does for a
        # cash-on-delivery order paid days after the parcel arrived.
        Order.objects.filter(pk=order.pk).update(
            status_updated_at=timezone.now()
        )

        assert send_review_request_emails.run()["sent"] == 1

    def test_a_status_change_that_is_not_a_delivery_is_no_delivery(
        self, customer
    ):
        order = delivered(customer, [product("alpha")])
        OrderHistory.objects.filter(order=order).update(
            new_value={"status": "SHIPPED"}
        )

        send_review_request_emails.run()

        assert mail.outbox == []

    def test_an_order_with_no_delivery_record_is_never_reminded(self, customer):
        order = delivered(customer, [product("alpha")])
        OrderHistory.objects.filter(order=order).delete()

        send_review_request_emails.run()

        assert mail.outbox == []

    def test_the_reminder_is_off_at_zero_days(self, customer):
        order = delivered(customer, [product("alpha")], days_ago=0.5)
        set_delay(0)

        assert delivery_window() is None
        assert send_review_request_emails.run() == {
            "sent": 0,
            "skipped": 0,
            "failed": 0,
        }
        assert mail.outbox == []
        assert not stamped(order)

    def test_the_store_chooses_the_delay(self, customer):
        delivered(customer, [product("alpha")], days_ago=3.5)
        set_delay(3)

        assert send_review_request_emails.run()["sent"] == 1


class TestOnce:
    def test_an_order_is_never_reminded_twice(self, customer):
        delivered(customer, [product("alpha")])

        first = send_review_request_emails.run()
        second = send_review_request_emails.run()

        assert first["sent"] == 1
        assert second == {"sent": 0, "skipped": 0, "failed": 0}
        assert len(mail.outbox) == 1

    def test_an_order_claimed_by_another_run_is_not_mailed(self, customer):
        order = delivered(customer, [product("alpha")])
        pending = Order.objects.filter(pk=order.pk)
        Order.objects.filter(pk=order.pk).update(
            review_request_sent_at=timezone.now()
        )

        with patch("order.tasks.awaiting_review_request", return_value=pending):
            assert send_review_request_emails.run()["sent"] == 0

        assert mail.outbox == []


class TestWhatIsAskedFor:
    def test_each_product_links_straight_to_its_review_form(self, customer):
        first, second = product("alpha"), product("beta")
        delivered(customer, [first, second])

        send_review_request_emails.run()

        [message] = mail.outbox
        for item in (first, second):
            assert f"{item.storefront_path}#write-review" in message.body

    def test_a_product_already_reviewed_is_not_asked_again(self, customer):
        reviewed, open_ = product("alpha"), product("beta")
        ProductReviewFactory(
            user=customer, product=reviewed, status=ReviewStatus.TRUE
        )
        delivered(customer, [reviewed, open_])

        send_review_request_emails.run()

        [message] = mail.outbox
        assert f"{open_.storefront_path}#write-review" in message.body
        assert reviewed.storefront_path not in message.body

    def test_a_review_still_awaiting_approval_counts_as_reviewed(
        self, customer
    ):
        item = product("alpha")
        ProductReviewFactory(
            user=customer, product=item, status=ReviewStatus.NEW
        )
        delivered(customer, [item])

        assert send_review_request_emails.run()["sent"] == 0

    def test_an_order_with_nothing_left_to_review_is_settled_silently(
        self, customer
    ):
        item = product("alpha")
        ProductReviewFactory(
            user=customer, product=item, status=ReviewStatus.TRUE
        )
        order = delivered(customer, [item])

        result = send_review_request_emails.run()

        assert result == {"sent": 0, "skipped": 1, "failed": 0}
        assert mail.outbox == []
        assert stamped(order)

    def test_a_store_with_reviews_off_sends_nothing(self, customer):
        order = delivered(customer, [product("alpha")])
        switch_store_settings(PRODUCT_REVIEWS_ENABLED=False)

        result = send_review_request_emails.run()

        assert result["sent"] == 0
        assert mail.outbox == []
        assert stamped(order)

    def test_a_guest_order_is_not_asked_when_a_purchase_is_required(
        self, customer
    ):
        order = delivered(customer, [product("alpha")])
        Order.objects.filter(pk=order.pk).update(user=None)

        assert send_review_request_emails.run()["sent"] == 0


class TestWhoIsAsked:
    def test_a_customer_who_opted_out_of_marketing_is_not_mailed(
        self, customer
    ):
        order = delivered(customer, [product("alpha")])
        optout.opt_out(
            order.email,
            EVERY_MARKETING_TYPE,
            source=EmailOptOut.Source.ONE_CLICK,
        )

        result = send_review_request_emails.run()

        assert result["sent"] == 0
        assert mail.outbox == []
        assert stamped(order)

    def test_the_store_can_switch_the_email_off(self, customer, store):
        order = delivered(customer, [product("alpha")])
        EmailSwitch.objects.create(type_key="order.review_request")
        # The switches are cached per cache generation, which the write
        # moves in the store's row; this test holds the row it loaded.
        store.cache_generation += 1

        assert send_review_request_emails.run()["sent"] == 0
        assert mail.outbox == []
        assert stamped(order)

    def test_the_email_carries_the_unsubscribe_headers(self, customer):
        delivered(customer, [product("alpha")])

        send_review_request_emails.run()

        [message] = mail.outbox
        assert "List-Unsubscribe" in message.extra_headers


class TestFailures:
    def test_a_transient_failure_is_retried_by_the_next_run(self, customer):
        order = delivered(customer, [product("alpha")])

        with patch(
            "order.tasks.send_email", side_effect=ConnectionError("down")
        ):
            result = send_review_request_emails.run()

        assert result == {"sent": 0, "skipped": 0, "failed": 1}
        assert not stamped(order)
        assert send_review_request_emails.run()["sent"] == 1

    def test_a_permanent_failure_is_not_retried(self, customer):
        order = delivered(customer, [product("alpha")])

        with patch(
            "order.tasks.send_email",
            side_effect=smtplib.SMTPResponseException(550, b"no such user"),
        ):
            result = send_review_request_emails.run()

        assert result["failed"] == 1
        assert stamped(order)

    def test_one_failing_order_does_not_stop_the_others(self, customer):
        delivered(customer, [product("alpha")])
        delivered(customer, [product("beta")])
        calls = iter([ConnectionError("down"), None])

        def flaky(*args, **kwargs):
            outcome = next(calls)
            if outcome is not None:
                raise outcome
            return send_real(*args, **kwargs)

        from emails.send import send_email as send_real

        with patch("order.tasks.send_email", side_effect=flaky):
            result = send_review_request_emails.run()

        assert result == {"sent": 1, "skipped": 0, "failed": 1}


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        (0, True),
        (8, True),
        (MAX_DELAY_DAYS, True),
        (MAX_DELAY_DAYS + 1, False),
        (-1, False),
        (True, False),
        (7.5, False),
        ("8", False),
        (None, False),
    ],
)
def test_the_delay_setting_is_a_whole_number_of_days(value, valid):
    assert validate_review_request_delay_days_setting(value) is valid
