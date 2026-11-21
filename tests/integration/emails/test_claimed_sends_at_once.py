"""Two runs of a mail task that checks a row and then sends mail once.

``deliver_gift_card_email`` (``delivered_at``) and the product alerts
(``notified_at``) are queued by more than one path: the purchase, the
admin's resend and the daily sweep for a card; every stock or price update
for an alert. Two runs, each a dispatch of its own (so no shared task id),
can meet. Each runs here on its own thread and database connection, as two
workers would (``tests.utils.concurrency``), and the transport holds the
first run mid-send.
"""

from __future__ import annotations

import time

import pytest
from django.core import mail

from giftcard.factories import GiftCardFactory
from giftcard.tasks import deliver_gift_card_email
from product.factories.product import ProductFactory
from product.models.alert import ProductAlert, ProductAlertKind
from product.tasks import send_product_alert_restock
from tests.utils.concurrency import in_thread, wait_for_a_blocked_backend
from tests.utils.emails import HeldTransport
from tests.utils.staff import bind_store_tenant

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("open_email_gates"),
]

WAIT = 10


@pytest.fixture(autouse=True)
def _outbox():
    mail.outbox.clear()


def run_in_the_store(tenant, task, args, task_id, failures):
    def run() -> None:
        bind_store_tenant(tenant)
        task.apply(args, task_id=task_id, throw=True)

    return in_thread(run, failures)


def eventually(condition) -> None:
    deadline = time.monotonic() + WAIT
    while not condition():
        assert time.monotonic() < deadline, "the condition never held"
        time.sleep(0.05)


def test_two_deliveries_of_one_card_at_once_mail_it_once(tenant):
    card = GiftCardFactory(recipient_email="friend@example.test")
    transport = HeldTransport(WAIT)
    failures: list[BaseException] = []

    with transport.patched():
        first = run_in_the_store(
            tenant, deliver_gift_card_email, [card.id], "purchase", failures
        )
        assert transport.sending.wait(WAIT)
        second = run_in_the_store(
            tenant, deliver_gift_card_email, [card.id], "sweep", failures
        )
        wait_for_a_blocked_backend(WAIT)

        transport.release.set()
        first.finish(WAIT)
        second.finish(WAIT)

    assert not failures, failures
    assert [m.to for m in mail.outbox] == [["friend@example.test"]]
    card.refresh_from_db()
    assert card.delivered_at is not None


def test_two_restock_runs_at_once_mail_each_alert_once(tenant):
    product = ProductFactory(stock=5, num_images=0, num_reviews=0)
    for guest in ("one@example.test", "two@example.test"):
        ProductAlert.objects.create(
            kind=ProductAlertKind.RESTOCK, product=product, email=guest
        )
    transport = HeldTransport(WAIT)
    failures: list[BaseException] = []

    with transport.patched():
        first = run_in_the_store(
            tenant, send_product_alert_restock, [product.id], "one", failures
        )
        assert transport.sending.wait(WAIT)
        # The first run holds its first alert's send; the second skips that
        # alert and sends the other.
        second = run_in_the_store(
            tenant, send_product_alert_restock, [product.id], "two", failures
        )
        eventually(lambda: len(transport.calls) == 2)

        transport.release.set()
        first.finish(WAIT)
        second.finish(WAIT)

    assert not failures, failures
    assert sorted(m.to[0] for m in mail.outbox) == [
        "one@example.test",
        "two@example.test",
    ]
    assert not ProductAlert.objects.filter(is_active=True).exists()
