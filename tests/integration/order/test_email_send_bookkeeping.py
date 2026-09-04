"""Two failure windows around the transactional email tasks.

Both reserve an idempotency flag on ``Order.metadata`` before sending,
and both consult that reservation ONLY on the first attempt — re-reading
it on a retry would block a legitimate retry after a transient relay
failure. That leaves two holes:

1. If every attempt fails, the reservation has to be released, or the
   flag outlives the send it stood in for and the customer never gets
   that email at all.
2. If something fails AFTER the message is already with the relay, the
   retry starts from the top with no reservation check and sends again.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from celery.exceptions import Retry

from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from order.tasks import (
    SHIPPING_NOTIFICATION_EMAIL_SENT_FLAG,
    send_shipping_notification_email,
)

pytestmark = pytest.mark.django_db


def _shipped_order():
    """A SHIPPED order with tracking, and no email already claimed.

    Written with ``queryset.update`` rather than ``save``: saving the
    status transition fires ``order_status_changed``, which under
    ``CELERY_TASK_ALWAYS_EAGER`` runs this very task, sends the mail and
    takes the reservation — so the call under test would find the flag
    set and no-op.
    """
    order = OrderFactory(num_order_items=0)
    Order.objects.filter(pk=order.pk).update(
        status=OrderStatus.SHIPPED,
        tracking_number="TRK-123456",
        shipping_carrier="ACS",
        metadata={},
    )
    order.refresh_from_db()
    return order


class TestReservationSurvivesOnlyASuccessfulSend:
    def test_exhausted_retries_release_the_flag(self):
        """The relay is down for every attempt.

        The flag must not be left behind: it would suppress the shipped
        email forever, including a later admin re-save of the tracking
        number, and the customer would never learn the parcel moved.

        Driven at ``retries == max_retries`` — the LAST attempt, the only
        one that reaches the terminal branch. Attempt 0 is what claims
        the slot, and it always retries while attempts remain, so the
        claim is simulated on the row here.
        """
        order = _shipped_order()
        Order.objects.filter(pk=order.pk).update(
            metadata={SHIPPING_NOTIFICATION_EMAIL_SENT_FLAG: True}
        )

        with patch(
            "order.tasks.EmailMultiAlternatives.send",
            side_effect=OSError("relay down"),
        ):
            result = send_shipping_notification_email.apply(
                args=[order.id], retries=3
            ).get()

        order.refresh_from_db()
        assert result is False
        assert SHIPPING_NOTIFICATION_EMAIL_SENT_FLAG not in (
            order.metadata or {}
        ), (
            "the reservation outlived a send that never happened — the "
            "customer can never be told the parcel shipped"
        )

    def test_a_retry_that_still_has_attempts_left_keeps_the_flag(self):
        """Only the terminal attempt releases; an intermediate failure
        must keep the claim so the retry is not raced by a second
        dispatch."""
        order = _shipped_order()
        Order.objects.filter(pk=order.pk).update(
            metadata={SHIPPING_NOTIFICATION_EMAIL_SENT_FLAG: True}
        )

        with (
            patch(
                "order.tasks.EmailMultiAlternatives.send",
                side_effect=OSError("relay down"),
            ),
            pytest.raises(Retry),
        ):
            send_shipping_notification_email.apply(
                args=[order.id], retries=1, throw=True
            ).get()

        order.refresh_from_db()
        assert order.metadata.get(SHIPPING_NOTIFICATION_EMAIL_SENT_FLAG)

    def test_a_successful_send_keeps_the_flag(self):
        order = _shipped_order()

        send_shipping_notification_email.apply(args=[order.id]).get()

        order.refresh_from_db()
        assert order.metadata.get(SHIPPING_NOTIFICATION_EMAIL_SENT_FLAG)


class TestFailureAfterTheMessageIsSent:
    def test_bookkeeping_failure_does_not_re_send(self):
        """The message is already with the relay and the history write
        then fails. Retrying would put a second "your order has shipped"
        in the customer's inbox, because the reservation is only
        consulted on the first attempt."""
        order = _shipped_order()

        with (
            patch(
                "order.tasks.OrderHistory.log_note",
                side_effect=RuntimeError("db blip"),
            ),
            patch("order.tasks.EmailMultiAlternatives.send") as send,
        ):
            result = send_shipping_notification_email.apply(
                args=[order.id]
            ).get()

        assert result is True
        assert send.call_count == 1, (
            "the email went out once; a failure after it must not re-send"
        )
        order.refresh_from_db()
        # The reservation stands: the customer HAS the email.
        assert order.metadata.get(SHIPPING_NOTIFICATION_EMAIL_SENT_FLAG)
