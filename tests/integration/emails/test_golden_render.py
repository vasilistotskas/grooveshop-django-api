"""The order emails as customers and the store read them today.

Characterisation, recorded before the email platform takes over the send
sites: each case runs a real Celery task over a fixed order and compares
the message that reaches ``mail.outbox`` (subject, recipients, From,
Reply-To, headers, attachments, HTML and text, in Greek and English) with
the files in ``golden/``. The migration of a send site onto the pipeline
must keep them equal; a deliberate change re-records them in the same
commit (``tests/utils/golden.py``).

Everything a body prints is pinned, so the files do not depend on the test
order, the seed or the day. ``OrderFactory`` draws names, addresses,
amounts, the status, the pay way and the payment status at random, and
pytest-randomly reseeds the generators each run, so ``build_order`` states
all of those. What cannot be fixed is normalised, and only that: the
footer's copyright year (``tests/utils/golden.py``).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.files.base import ContentFile
from django.utils import translation
from django.utils.translation import gettext
from djmoney.money import Money
from phonenumber_field.phonenumber import PhoneNumber

from country.models import Country
from order.emails import order_status_update_email
from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import RefundTransactionFactory
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.models import Invoice, Order, RetailCredit
from order.models.retail_credit import CreditReason
from order.tasks import (
    send_admin_new_order_email,
    send_credit_email,
    send_dispute_notification_email,
    send_invoice_email,
    send_order_confirmation_email,
    send_order_status_update_email,
    send_payment_failed_email,
    send_refund_confirmation_email,
    send_shipping_notification_email,
)
from pay_way.enum.pay_way import PayWayEnum
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from region.models import Region
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.tasks import acs_send_arrival_notification
from shipping_boxnow.factories import (
    BoxNowLockerFactory,
    BoxNowParcelEventFactory,
    BoxNowShipmentFactory,
)
from shipping_boxnow.tasks import boxnow_send_arrival_notification
from tests.utils.golden import assert_golden, snapshot, stable
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

GOLDEN = Path(__file__).parent / "golden"

STORE_INBOX = "owner@store.test"
CUSTOMER_INBOX = "customer@example.test"
ORDER_ID = 900_001
PLACED_AT = datetime(2026, 3, 14, 10, 30, tzinfo=UTC)

#: Two lines, named in both languages: the language of the mail is then
#: visible in every product name it prints.
PRODUCTS = (
    {
        "names": {"el": "Ασύρματα ακουστικά", "en": "Wireless earbuds"},
        "price": Decimal("20.00"),
        "quantity": 1,
    },
    {
        "names": {"el": "Φορτιστής επαφής", "en": "Charging pad"},
        "price": Decimal("31.50"),
        "quantity": 2,
    },
)


#: What the pay way tells the customer, in each language. The factory draws
#: its description and instructions at random.
PAY_WAY_TEXTS = {
    "el": {
        "description": "Πληρωμή με μεταφορά",
        "instructions": "<div>Κάντε κατάθεση στον λογαριασμό μας.</div>",
    },
    "en": {
        "description": "Pay by transfer",
        "instructions": "<div>Deposit to our bank account.</div>",
    },
}


@pytest.fixture(params=["el", "en"])
def language(request):
    return request.param


def pay_way(kind: str = "offline"):
    # The factory hands out ``key`` from an iterator, so an unpinned key,
    # and the method name the store's alert prints from it, depends on
    # how many pay ways the shard created before this test.
    created = (
        PayWayFactory.create_online_payment(key=PayWayEnum.STRIPE)
        if kind == "online"
        else PayWayFactory.create_offline_payment(key=PayWayEnum.BANK_TRANSFER)
    )
    for code, texts in PAY_WAY_TEXTS.items():
        created.set_current_language(code)
        created.description = texts["description"]
        created.instructions = texts["instructions"]
        created.save()
    return created


@pytest.fixture(autouse=True)
def _greek_catalogue_is_compiled():
    """The Greek files are only worth recording from a compiled catalogue:
    without ``compilemessages`` gettext falls back to the English source and
    an ``el`` file would silently hold English."""
    with translation.override("el"):
        assert gettext("Order Received - #{order_id}") != (
            "Order Received - #{order_id}"
        ), "run `uv run python manage.py compilemessages --ignore=.venv`"


@pytest.fixture(autouse=True)
def _store(bind_tenant, settings, language):
    """A bound store whose language is the one under test, a platform
    sender and storefront that do not follow the developer's ``.env``, and
    no suggestion strip (``recommendation`` has its own tests, and the
    strip is drawn from whatever products the database holds)."""
    settings.NUXT_BASE_URL = "https://shop.example"
    settings.DEFAULT_FROM_EMAIL = "noreply@platform.test"
    bind_tenant(
        SimpleNamespace(
            schema_name="acme",
            default_locale=language,
            store_name="Acme Gadgets",
            contact_email=STORE_INBOX,
        )
    )
    with patch("order.emails.order_email_strip", return_value=None):
        yield
    mail.outbox.clear()


def build_order(language: str, *, items: bool = True, **fields) -> Order:
    """A fixed order: every field an email prints is stated.

    Written in two steps. Creating it fires the signal-driven emails (the
    confirmation, with its idempotency flag), so the state the case needs,
    the status above all, is set with ``update()`` afterwards, which
    signals nothing, and the outbox is emptied.
    """
    defaults = {
        "id": ORDER_ID,
        "user": UserAccountFactory(
            num_addresses=0,
            email=CUSTOMER_INBOX,
            first_name="Maria",
            last_name="Papadopoulou",
            language_code=language,
        ),
        "language_code": language,
        "country": Country.objects.get(alpha_2="GR"),
        "region": Region.objects.filter(country_id="GR").order_by("pk").first(),
        "pay_way": pay_way(),
        "email": CUSTOMER_INBOX,
        "first_name": "Maria",
        "last_name": "Papadopoulou",
        "street": "Ermou",
        "street_number": "12",
        "city": "Athens",
        "zipcode": "10563",
        "place": "",
        "floor": "",
        "location_type": "",
        "phone": PhoneNumber.from_string("+302101234567"),
        "customer_notes": "",
        "shipping_price": Money(Decimal("4.90"), "EUR"),
        "amount_due": Money(Decimal(0), "EUR"),
        "document_type": OrderDocumentTypeEnum.RECEIPT.value,
        "payment_status": PaymentStatus.PENDING,
        "payment_id": "",
        "payment_method": "",
        "tracking_number": "",
        "shipping_carrier": "",
        "status_updated_at": None,
    }
    status = fields.pop("status", OrderStatus.PROCESSING)
    order = OrderFactory(num_order_items=0, **{**defaults, **fields})
    if items:
        for line in PRODUCTS:
            product = ProductFactory(
                num_images=0,
                num_reviews=0,
                price=Money(line["price"], "EUR"),
            )
            for code, name in line["names"].items():
                product.set_current_language(code)
                product.name = name
                product.save()
            OrderItemFactory(
                order=order,
                product=product,
                price=Money(line["price"], "EUR"),
                quantity=line["quantity"],
                is_refunded=False,
                refunded_quantity=0,
                notes="",
            )
    Order.objects.filter(pk=order.pk).update(
        status=status, created_at=PLACED_AT, metadata={}
    )
    mail.outbox.clear()
    return Order.objects.get(pk=order.pk)


def assert_sent(name: str, language: str) -> None:
    [message] = mail.outbox
    assert_golden(GOLDEN, name, language, snapshot(message))


class TestCustomerOrderEmails:
    def test_order_received(self, language):
        order = build_order(language)

        assert send_order_confirmation_email.run(order.id) is True

        assert_sent("order_received", language)

    def test_order_payment_confirmed(self, language):
        order = build_order(
            language,
            pay_way=pay_way("online"),
            payment_status=PaymentStatus.COMPLETED,
        )

        assert send_order_confirmation_email.run(order.id) is True

        assert_sent("order_payment_confirmed", language)

    def test_order_shipped(self, language):
        order = build_order(
            language,
            status=OrderStatus.SHIPPED,
            tracking_number="TRK-0001",
            shipping_carrier="ACS",
        )

        assert send_shipping_notification_email.run(order.id) is True

        assert_sent("order_shipped", language)

    @pytest.mark.parametrize(
        "status",
        [
            OrderStatus.DELIVERED,
            OrderStatus.COMPLETED,
            OrderStatus.CANCELED,
            OrderStatus.RETURNED,
        ],
    )
    def test_status_update(self, language, status):
        order = build_order(language, status=status)

        assert send_order_status_update_email.run(order.id, status) is True

        assert_sent(f"order_{status.lower()}", language)

    def test_payment_failed(self, language):
        order = build_order(language, payment_status=PaymentStatus.FAILED)

        assert send_payment_failed_email.run(order.id) is True

        assert_sent("payment_failed", language)

    def test_refund_confirmation(self, language):
        order = build_order(language, payment_status=PaymentStatus.COMPLETED)
        refund = RefundTransactionFactory(
            order=order,
            amount=Money(Decimal("10.00"), "EUR"),
            charge__amount=Money(Decimal("50.00"), "EUR"),
        )
        mail.outbox.clear()

        assert send_refund_confirmation_email.run(refund.id) is True

        assert_sent("order_refunded", language)

    def test_receipt_with_its_pdf(self, language):
        order = build_order(language, status=OrderStatus.SHIPPED)
        invoice = Invoice.objects.create(
            order=order,
            invoice_number="INV-2026-000001",
            issue_date=date(2026, 3, 15),
            mydata_mark=400000000000001,
        )
        invoice.document_file.save("receipt.pdf", ContentFile(b"%PDF-1.4"))

        assert send_invoice_email.run(order.id) is True

        assert_sent("retail_document_issued_receipt", language)

    def test_credit_with_its_pdf(self, language):
        order = build_order(language, status=OrderStatus.SHIPPED)
        invoice = Invoice.objects.create(
            order=order,
            invoice_number="INV-2026-000001",
            issue_date=date(2026, 3, 15),
            mydata_mark=400000000000001,
        )
        credit = RetailCredit.objects.create(
            order=order,
            invoice=invoice,
            reason=CreditReason.SETTLEMENT,
            credit_number="CRD-2026-000001",
            issue_date=date(2026, 3, 20),
            mydata_mark=400000000000002,
        )
        credit.document_file.save("credit.pdf", ContentFile(b"%PDF-1.4"))

        with patch(
            "order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4"
        ):
            assert send_credit_email.run(credit.id) is True

        assert_sent("retail_document_issued_credit", language)

    def test_acs_out_for_delivery(self, language):
        order = build_order(language)
        shipment = AcsShipmentFactory(
            order=order, with_voucher=True, voucher_no="7000000001"
        )

        acs_send_arrival_notification.run(shipment.id)

        assert_sent("acs_out_for_delivery", language)

    def test_boxnow_parcel_at_locker(self, language):
        order = build_order(language)
        locker = BoxNowLockerFactory(
            external_id="apm-0001",
            name="Locker Stadiou",
            title="Locker Stadiou",
            address_line_1="Stadiou 5",
            postal_code="10564",
        )
        shipment = BoxNowShipmentFactory(
            order=order,
            locker=locker,
            with_parcel=True,
            parcel_id="9200000001",
        )
        event = BoxNowParcelEventFactory(shipment=shipment)

        boxnow_send_arrival_notification.run(event.id)

        assert_sent("boxnow_parcel_at_locker", language)


class TestStoreOrderEmails:
    def test_new_order_alert(self, language):
        order = build_order(language)

        assert send_admin_new_order_email.run(order.id) is True

        assert_sent("admin_new_order", language)

    def test_dispute_opened(self, language):
        order = build_order(
            language,
            payment_status=PaymentStatus.COMPLETED,
            metadata={"dispute_reason": "fraudulent"},
        )

        assert (
            send_dispute_notification_email.run(
                order.id, "dp_0001", "stripe", lost=False
            )
            is True
        )

        assert_sent("dispute_notification_opened", language)

    def test_dispute_lost(self, language):
        order = build_order(language, payment_status=PaymentStatus.COMPLETED)
        charge = RefundTransactionFactory(order=order).charge
        order.payment_transactions.create(
            kind=PaymentTransactionKind.CHARGEBACK,
            amount=Money(Decimal("50.00"), "EUR"),
            provider="stripe",
            provider_reference="dp_0001",
            charge=charge,
        )
        mail.outbox.clear()

        assert (
            send_dispute_notification_email.run(
                order.id, "dp_0001", "stripe", lost=True
            )
            is True
        )

        assert_sent("dispute_notification_lost", language)


def test_the_generic_status_template_is_recorded_from_its_builder(language):
    """No task reaches ``order_status_generic`` any more (every status that
    emails has its own template), so there is no sent message to compare.
    Its bodies are still recorded, because the migration decides whether
    the template goes (``order.emails.ORDER_EMAIL_KINDS``)."""
    order = build_order(language)

    email = order_status_update_email(
        order, OrderStatus.PROCESSING, language=language
    )
    text, html = email.render()

    assert_golden(
        GOLDEN,
        "order_status_generic",
        language,
        {
            "json": json.dumps({"subject": email.subject}, ensure_ascii=False)
            + "\n",
            "html": stable(html),
            "txt": stable(text),
        },
    )
