"""The admin preview of an order email IS the email that order is sent.

The preview used to keep its own copy of everything: an English subject
typed into ``core/email/config.py`` for nine templates and "Email from
{store}" for the rest, and a reduced context rebuilt by hand from the
order (prices pre-formatted into strings, no pay way, no items queryset,
no tracking context), so it showed a message nobody received. The subject
and the context are now built by the registered email type
(``order.emails``) and rendered by ``emails.render.render``, the function
the sending pipeline and the preview both call.

Each test sends the real email through its real task, previews the same
order, and compares what a customer would read.
"""

from __future__ import annotations

import pytest
from django.core import mail

from core.email.preview_service import EmailTemplatePreviewService
from emails.registry import registry
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import RefundTransactionFactory
from order.factories.order import OrderFactory
from order.models import Order
from order.tasks import (
    send_admin_new_order_email,
    send_order_confirmation_email,
    send_order_status_update_email,
    send_payment_failed_email,
    send_refund_confirmation_email,
    send_shipping_notification_email,
)
from pay_way.factories import PayWayFactory
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)

pytestmark = pytest.mark.django_db

STORE_INBOX = "owner@store.test"


@pytest.fixture(autouse=True)
def _store(settings):
    settings.NUXT_BASE_URL = "https://shop.example"
    previous = bind_store_tenant(
        store_tenant(
            "acme",
            default_locale="en",
            store_name="Acme Gadgets",
            contact_email=STORE_INBOX,
            owner_email=STORE_INBOX,
        )
    )
    mail.outbox.clear()
    yield
    unbind_store_tenant(previous)


def _html(message) -> str:
    [(html, mimetype)] = message.alternatives
    assert mimetype == "text/html"
    return html


def _preview(template: str, order, language: str):
    preview = EmailTemplatePreviewService().generate_preview(
        template, order_id=order.id, language=language
    )
    assert preview.error is None
    assert preview.is_sample_data is False
    return preview


def _assert_same_email(preview, sent) -> None:
    assert preview.subject == sent.subject
    assert preview.text_content == sent.body
    assert preview.html_content == _html(sent)


@pytest.mark.parametrize("language", ["en", "el"])
class TestPreviewEqualsTheSentEmail:
    def _confirmation_sent(self, order):
        # Creating the order already sent its confirmation, once.
        Order.objects.filter(pk=order.pk).update(metadata={})
        mail.outbox.clear()
        assert send_order_confirmation_email.run(order.id) is True
        return mail.outbox[-1]

    # OrderFactory draws its pay way and payment status at random, and
    # the sender picks "payment confirmed" over "received" for a
    # captured online payment, so each case states the one it needs.
    def test_order_received(self, language):
        order = OrderFactory(
            num_order_items=1,
            language_code=language,
            pay_way=PayWayFactory.create_offline_payment(),
            payment_status=PaymentStatus.PENDING,
        )

        sent = self._confirmation_sent(order)

        _assert_same_email(_preview("order_received", order, language), sent)

    def test_order_payment_confirmed(self, language):
        order = OrderFactory(
            num_order_items=1,
            language_code=language,
            pay_way=PayWayFactory.create_online_payment(),
            payment_status=PaymentStatus.COMPLETED,
        )

        sent = self._confirmation_sent(order)

        _assert_same_email(
            _preview("order_payment_confirmed", order, language), sent
        )

    def test_order_shipped(self, language):
        order = OrderFactory(
            num_order_items=1,
            language_code=language,
            status=OrderStatus.SHIPPED,
            tracking_number="TRK-0001",
            shipping_carrier="ACS",
        )
        mail.outbox.clear()

        assert send_shipping_notification_email.run(order.id) is True

        sent = mail.outbox[-1]
        _assert_same_email(_preview("order_shipped", order, language), sent)

    def test_payment_failed(self, language):
        order = OrderFactory(num_order_items=1, language_code=language)
        mail.outbox.clear()

        assert send_payment_failed_email.run(order.id) is True

        sent = mail.outbox[-1]
        _assert_same_email(_preview("payment_failed", order, language), sent)

    def test_status_update(self, language):
        order = OrderFactory(
            num_order_items=1,
            language_code=language,
            status=OrderStatus.CANCELED,
        )
        mail.outbox.clear()

        assert send_order_status_update_email.run(order.id, "CANCELED")

        sent = mail.outbox[-1]
        _assert_same_email(_preview("order_canceled", order, language), sent)

    def test_refund(self, language):
        order = OrderFactory(num_order_items=1, language_code=language)
        refund = RefundTransactionFactory(order=order)
        mail.outbox.clear()

        assert send_refund_confirmation_email.run(refund.id) is True

        sent = mail.outbox[-1]
        _assert_same_email(_preview("order_refunded", order, language), sent)

    def test_the_stores_new_order_alert(self, language):
        """Staff mail is in the STORE's language whatever the order's."""
        order = OrderFactory(num_order_items=1, language_code=language)
        mail.outbox.clear()

        assert send_admin_new_order_email.run(order.id) is True

        sent = mail.outbox[-1]
        _assert_same_email(_preview("admin_new_order", order, "en"), sent)


class TestThePreviewRendersTheRealContext:
    def test_the_order_is_the_model_not_a_reduced_copy(self):
        order = OrderFactory(num_order_items=1)

        preview = _preview("order_received", order, "en")

        context = preview.context_data
        assert isinstance(context["order"], Order)
        assert context["order"].pk == order.pk
        # What the sender passes and the hand-built copy never had.
        assert {"pay_way", "is_paid", "payment_reference_needed"} <= set(
            context
        )

    def test_the_subject_is_translated_into_the_chosen_language(self):
        order = OrderFactory(num_order_items=0)

        english = _preview("order_shipped", order, "en").subject
        greek = _preview("order_shipped", order, "el").subject

        assert english == f"Your Order #{order.id} Has Shipped"
        assert greek == f"Η παραγγελία σας #{order.id} έχει αποσταλεί"

    def test_no_subject_is_made_up_for_an_email_without_a_type(self):
        """A template no registered email type sends shows NO subject,
        not "Email from {store}"."""
        preview = EmailTemplatePreviewService().generate_preview(
            "confirmation", language="en"
        )

        assert preview.error is None
        assert preview.subject == ""

    def test_a_sample_preview_still_shows_the_real_subject(self):
        preview = EmailTemplatePreviewService().generate_preview(
            "order_shipped", language="en"
        )

        assert preview.is_sample_data is True
        assert preview.subject.startswith("Your Order #")
        assert preview.subject.endswith(" Has Shipped")

    def test_an_order_with_nothing_to_describe_is_reported(self):
        order = OrderFactory(num_order_items=0)

        preview = EmailTemplatePreviewService().generate_preview(
            "order_refunded", order_id=order.id, language="en"
        )

        assert preview.error == "This order has no refund to describe."
        assert preview.html_content == ""

    def test_every_order_email_template_is_sent_by_a_registered_type(self):
        """A template added to ``emails/order/`` is previewable only through
        the type that sends it (the registry refuses one no type names)."""
        from pathlib import Path

        from django.conf import settings

        on_disk = {
            path.stem
            for path in (
                Path(settings.BASE_DIR) / "core/templates/emails/order"
            ).glob("*.html")
        }

        assert on_disk == {
            email_type.templates.rpartition("/")[2]
            for email_type in registry.all_types()
            if email_type.templates.startswith("emails/order/")
        }


class TestProductNamesFollowTheEmailsLanguage:
    """parler fixes a model instance's language when it is created. The
    confirmation task prefetched ``items__product__translations`` before
    the email's language was chosen, so an English customer's order named
    every product in the worker's language (Greek)."""

    def test_confirmation_names_products_in_the_orders_language(self):
        order = OrderFactory(num_order_items=1, language_code="en")
        Order.objects.filter(pk=order.pk).update(metadata={})
        product = order.items.get().product
        english = product.safe_translation_getter(
            "name", language_code="en", any_language=False
        )
        greek = product.safe_translation_getter(
            "name", language_code="el", any_language=False
        )
        assert english != greek
        mail.outbox.clear()

        assert send_order_confirmation_email.run(order.id) is True

        body = mail.outbox[-1].body
        assert english in body
        assert greek not in body


class TestEveryRegisteredTypePreviewsFromItsSample:
    """Without an order the preview renders the type's own ``sample``, an
    unsaved object graph, through the same ``render`` the pipeline calls."""

    @pytest.mark.parametrize("language", ["en", "el"])
    @pytest.mark.parametrize(
        "email_type",
        registry.all_types(),
        ids=lambda email_type: email_type.key,
    )
    def test_the_sample_renders_with_its_real_subject(
        self, email_type, language
    ):
        template = email_type.templates.rpartition("/")[2]

        preview = EmailTemplatePreviewService().generate_preview(
            template, language=language
        )

        assert preview.error is None
        assert preview.is_sample_data is True
        assert preview.subject
        assert "{" not in preview.subject
        assert preview.html_content
        assert preview.text_content
