"""``emails.0002``: an account that opted out before ``EmailOptOut`` existed.

Until the blanket unsubscribe link also wrote ``EmailOptOut`` (#368), a
customer's refusal lived only on ``UserAccount.marketing_opted_out_at``. The
pipeline reads ``EmailOptOut`` alone, so the migration copies the flag into a
``*`` row under the pipeline's own address hash. These tests run its function
over the state those accounts are in: the flag and no row.
"""

from __future__ import annotations

import importlib
from datetime import timedelta

import pytest
from django.apps import apps
from django.core import mail
from django.utils import timezone

from cart.factories.cart import CartFactory
from core.tasks import send_inactive_user_notifications
from emails import optout
from emails.models import EVERY_MARKETING_TYPE, EmailOptOut
from order.models.stock_reservation import StockReservation
from order.tasks import send_checkout_abandonment_emails
from product.factories.product import ProductFactory
from product.models.alert import ProductAlert, ProductAlertKind
from product.tasks import send_product_alert_restock
from user.factories.account import UserAccountFactory
from user.models.account import UserAccount

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("open_email_gates"),
]

migration = importlib.import_module(
    "emails.migrations.0002_import_marketing_opt_outs"
)


def run_migration() -> None:
    migration.import_opt_outs(apps, None)


@pytest.fixture
def leaver():
    """An account that opted out before #368: the flag, and no row."""
    user = UserAccountFactory(
        num_addresses=0,
        language_code="en",
        marketing_opted_out_at=timezone.now(),
        last_login=timezone.now() - timedelta(days=400),
        reengagement_email_count=0,
        last_reengagement_email_at=None,
    )
    assert not EmailOptOut.objects.exists()
    mail.outbox.clear()
    return user


class TestTheImport:
    def test_a_flagged_account_gets_a_row_under_the_pipelines_hash(
        self, leaver
    ):
        run_migration()

        row = EmailOptOut.objects.get()
        assert row.address_hash == optout.address_hash(leaver.email)
        assert row.type_key == EVERY_MARKETING_TYPE

    def test_an_account_that_never_opted_out_gets_none(self, leaver):
        UserAccountFactory(num_addresses=0)

        run_migration()

        assert EmailOptOut.objects.count() == 1

    def test_it_can_run_twice(self, leaver):
        run_migration()
        run_migration()

        assert EmailOptOut.objects.count() == 1

    def test_an_existing_row_is_left_as_it_was(self, leaver):
        optout.opt_out(
            leaver.email,
            EVERY_MARKETING_TYPE,
            source=EmailOptOut.Source.ONE_CLICK,
        )

        run_migration()

        assert EmailOptOut.objects.get().source == "one_click"


class TestAfterTheImportOnlyTheRowDecides:
    def test_no_restock_alert(self, leaver):
        product = ProductFactory(stock=5, num_images=0, num_reviews=0)
        ProductAlert.objects.create(
            kind=ProductAlertKind.RESTOCK, product=product, user=leaver
        )
        run_migration()
        mail.outbox.clear()

        send_product_alert_restock.run(product.pk)

        assert mail.outbox == []

    def test_no_reengagement_mail(self, leaver):
        run_migration()

        send_inactive_user_notifications()

        assert [m for m in mail.outbox if leaver.email in m.to] == []

    def test_no_abandoned_checkout_mail(self, leaver):
        cart = CartFactory(user=leaver, num_cart_items=0)
        product = ProductFactory(stock=5, num_images=0, num_reviews=0)
        reservation = StockReservation.objects.create(
            product=product,
            quantity=1,
            reserved_by=leaver,
            session_id=str(cart.uuid),
            expires_at=timezone.now() - timedelta(hours=5),
            consumed=True,
            abandonment_notified=False,
            order=None,
        )
        StockReservation.objects.filter(pk=reservation.pk).update(
            updated_at=timezone.now() - timedelta(hours=5)
        )
        run_migration()
        mail.outbox.clear()

        assert send_checkout_abandonment_emails.run() == 0

        assert mail.outbox == []

    def test_the_flag_alone_no_longer_stops_a_mail(self, leaver):
        """Before the import the flag is not read: the migration is what
        makes the row, and the row the one gate."""
        send_inactive_user_notifications()

        assert [m for m in mail.outbox if leaver.email in m.to]
        assert UserAccount.objects.get(pk=leaver.pk).marketing_opted_out_at
