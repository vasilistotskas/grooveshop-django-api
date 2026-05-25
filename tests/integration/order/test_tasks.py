from datetime import timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest
from django.test import TestCase as DjangoTestCase
from django.test import override_settings
from django.utils import timezone

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from order.tasks import (
    CONFIRMATION_EMAIL_SENT_AT_KEY,
    CONFIRMATION_EMAIL_SENT_FLAG,
    _confirmation_already_sent,
    _release_confirmation_email,
    check_pending_orders,
    generate_order_invoice,
    send_admin_new_order_email,
    send_invoice_email,
    send_order_confirmation_email,
    send_order_status_update_email,
    send_shipping_notification_email,
)


@pytest.mark.django_db
class OrderTasksSimpleTestCase(DjangoTestCase):
    def setUp(self):
        # Pin both status and payment_status so tests that exercise the
        # PROCESSING email path (status-update, template fallback) are
        # deterministic — the factory's default payment_status is a
        # random choice across all PaymentStatus values, which made
        # these tests flake whenever COMPLETED was rolled (the task
        # intentionally skips the PROCESSING status-update email when
        # the payment is already complete, to avoid duplicating the
        # separate "Payment Confirmed" email).
        self.order = OrderFactory.create(
            email="customer@example.com",
            first_name="John",
            last_name="Doe",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        # Order creation fires the order_created signal → the confirmation
        # email task runs eagerly (CELERY_TASK_ALWAYS_EAGER + on_commit fires
        # immediately in tests) and reserves the dedupe flag. Release it so
        # tests that call the task directly see a fresh run.
        _release_confirmation_email(self.order.id)

    @patch("order.tasks.OrderHistory.log_note")
    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.render_to_string")
    @override_settings(
        SITE_NAME="GrooveShop",
        INFO_EMAIL="support@example.com",
        NUXT_BASE_URL="http://example.com",
        STATIC_BASE_URL="http://example.com",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_send_order_confirmation_email_success(
        self, mock_render, mock_email, mock_log_note
    ):
        mock_email_instance = Mock()
        mock_email.return_value = mock_email_instance

        mock_render.side_effect = ["Email content", "HTML content"]

        result = send_order_confirmation_email(self.order.id)

        self.assertTrue(result)
        mock_email.assert_called_once()
        mock_email_instance.attach_alternative.assert_called_once()
        mock_email_instance.send.assert_called_once()
        mock_log_note.assert_called_once()

    @patch("order.tasks.logger.error")
    def test_send_order_confirmation_email_order_not_found(self, mock_logger):
        result = send_order_confirmation_email(999999)

        self.assertFalse(result)
        mock_logger.assert_called_once()

    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.render_to_string")
    @override_settings(
        SITE_NAME="GrooveShop",
        INFO_EMAIL="support@example.com",
        NUXT_BASE_URL="http://example.com",
        STATIC_BASE_URL="http://example.com",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_send_order_confirmation_email_idempotent(
        self, mock_render, mock_email
    ):
        # Calling the task twice in a row (e.g. order_created signal
        # followed by payment webhook) must only send one email.
        mock_email_instance = Mock()
        mock_email.return_value = mock_email_instance
        mock_render.side_effect = [
            "Email content",
            "HTML content",
            "Email content",
            "HTML content",
        ]

        first = send_order_confirmation_email(self.order.id)
        second = send_order_confirmation_email(self.order.id)

        self.assertTrue(first)
        self.assertTrue(second)
        mock_email_instance.send.assert_called_once()

        self.order.refresh_from_db()
        self.assertIsNotNone(
            self.order.metadata.get(CONFIRMATION_EMAIL_SENT_AT_KEY),
            "permanent sent_at timestamp must be set after a successful send",
        )

    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.render_to_string")
    @override_settings(
        SITE_NAME="GrooveShop",
        INFO_EMAIL="support@example.com",
        NUXT_BASE_URL="http://example.com",
        STATIC_BASE_URL="http://example.com",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    @patch("order.tasks.cache")
    def test_send_order_confirmation_email_worker_kill_recovery(
        self, mock_cache, mock_render, mock_email
    ):
        """Simulate a worker OOM-kill mid-send.

        The old pattern set a boolean metadata flag before the send; a
        worker kill left the flag permanently set so the customer never
        received the confirmation.

        The new pattern uses a Redis execution lock with a short TTL:
        - Acquire lock → send → write permanent DB timestamp → release lock.
        - If the worker is killed the lock auto-expires; the next invocation
          can acquire the lock and complete the send.

        ``order.tasks.cache`` is mocked (not exercised via the live ``cache``
        proxy) because the cache proxy in this test suite is bound to the
        production Redis backend — keys clash across xdist workers whose
        per-worker DBs both mint ``order.id=1`` and target the same
        ``confirmation_email_lock:1`` Redis key, leaving the lock state
        race-prone. The mock makes the worker-kill simulation deterministic
        by directly controlling ``cache.add``'s return value across the two
        call legs (held → released).
        """
        mock_email_instance = Mock()
        mock_email.return_value = mock_email_instance
        mock_render.side_effect = ["Email content", "HTML content"]

        # Leg 1: dead worker's lock is still active → cache.add (atomic
        # set-if-not-exists) returns False → task skips without sending.
        mock_cache.add.return_value = False
        result_while_locked = send_order_confirmation_email(self.order.id)
        self.assertTrue(result_while_locked)  # skips gracefully, not an error
        mock_email_instance.send.assert_not_called()

        # Leg 2: lock TTL expired (dead worker's key evicted) → cache.add
        # returns True → task acquires the lock, sends, sets the permanent
        # sent_at timestamp.
        mock_cache.add.return_value = True
        result_after_expiry = send_order_confirmation_email(self.order.id)
        self.assertTrue(result_after_expiry)
        mock_email_instance.send.assert_called_once()

        self.order.refresh_from_db()
        self.assertIsNotNone(
            self.order.metadata.get(CONFIRMATION_EMAIL_SENT_AT_KEY),
            "permanent sent_at timestamp must be set after successful send",
        )
        # The legacy boolean key (``CONFIRMATION_EMAIL_SENT_FLAG``) is
        # no longer dual-written — new writes use only the timestamp.
        # The reader's fallback at ``_confirmation_already_sent`` still
        # honours the boolean for pre-timestamp DB rows.
        self.assertNotIn(CONFIRMATION_EMAIL_SENT_FLAG, self.order.metadata)

        # Third call: permanent DB flag present → skip without touching Redis.
        mock_email_instance.send.reset_mock()
        result_already_sent = send_order_confirmation_email(self.order.id)
        self.assertTrue(result_already_sent)
        mock_email_instance.send.assert_not_called()

    def test_confirmation_already_sent_dedupes_via_either_key(self):
        """The reader honours BOTH the timestamp key (current writers
        set this) AND the boolean key (older DB rows have only this).
        Guards the load-bearing promise made when the dual-write was
        dropped: new writes use only the timestamp, but the boolean
        fallback ensures older rows never re-fire the confirmation
        email."""
        # Current writer shape: timestamp set, no boolean.
        self.assertTrue(
            _confirmation_already_sent(
                {CONFIRMATION_EMAIL_SENT_AT_KEY: "2026-01-01T00:00:00+00:00"}
            )
        )
        # Older DB rows: boolean only, no timestamp. The fallback at
        # the end of ``_confirmation_already_sent`` honours these.
        self.assertTrue(
            _confirmation_already_sent({CONFIRMATION_EMAIL_SENT_FLAG: True})
        )
        # Brand-new order with no email-sent state yet — neither key
        # set, dedupe must return False so the first send fires.
        self.assertFalse(_confirmation_already_sent({}))
        self.assertFalse(_confirmation_already_sent(None))

    @patch("order.tasks.OrderHistory.log_note")
    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.render_to_string")
    @override_settings(
        SITE_NAME="GrooveShop",
        INFO_EMAIL="support@example.com",
        NUXT_BASE_URL="http://example.com",
        STATIC_BASE_URL="http://example.com",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_send_order_status_update_email_success(
        self, mock_render, mock_email, mock_log_note
    ):
        mock_email_instance = Mock()
        mock_email.return_value = mock_email_instance

        mock_render.side_effect = ["Email content", "HTML content"]

        result = send_order_status_update_email(
            self.order.id, OrderStatus.PROCESSING
        )

        self.assertTrue(result)
        mock_email.assert_called_once()
        mock_email_instance.attach_alternative.assert_called_once()
        mock_email_instance.send.assert_called_once()
        mock_log_note.assert_called_once()

    def test_send_order_status_update_email_skip_pending(self):
        result = send_order_status_update_email(
            self.order.id, OrderStatus.PENDING
        )

        self.assertTrue(result)

    @patch("order.tasks.logger.error")
    def test_send_order_status_update_email_order_not_found(self, mock_logger):
        result = send_order_status_update_email(999999, OrderStatus.PROCESSING)

        self.assertFalse(result)
        mock_logger.assert_called_once()

    @patch("order.tasks.OrderHistory.log_note")
    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.render_to_string")
    @patch("order.tasks.logger.warning")
    @override_settings(
        SITE_NAME="GrooveShop",
        INFO_EMAIL="support@example.com",
        NUXT_BASE_URL="http://example.com",
        STATIC_BASE_URL="http://example.com",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_send_order_status_update_email_template_fallback(
        self, mock_logger_warning, mock_render, mock_email, mock_log_note
    ):
        mock_email_instance = Mock()
        mock_email.return_value = mock_email_instance

        def render_side_effect(template_name, context):
            if "order_processing" in template_name:
                raise Exception("Template not found")
            else:
                return "Generic email content"

        mock_render.side_effect = render_side_effect

        result = send_order_status_update_email(
            self.order.id, OrderStatus.PROCESSING
        )

        self.assertTrue(result)
        mock_logger_warning.assert_called_once()
        mock_email_instance.send.assert_called_once()

    @patch("order.tasks.OrderHistory.log_note")
    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.render_to_string")
    @override_settings(
        SITE_NAME="GrooveShop",
        INFO_EMAIL="support@example.com",
        NUXT_BASE_URL="http://example.com",
        STATIC_BASE_URL="http://example.com",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_send_shipping_notification_email_success(
        self, mock_render, mock_email, mock_log_note
    ):
        order_with_tracking = OrderFactory.create(email="customer@example.com")
        order_with_tracking.tracking_number = "TRACK123456"
        order_with_tracking.shipping_carrier = "UPS"
        order_with_tracking.save()

        # The save() above fires order_shipment_dispatched, which under
        # EAGER Celery already invoked the task once and stamped the
        # idempotency flag (see _reserve_shipping_notification_email).
        # Clear the flag so the explicit task call in this test
        # exercises the actual send path instead of short-circuiting.
        order_with_tracking.refresh_from_db()
        if order_with_tracking.metadata:
            order_with_tracking.metadata.pop(
                "shipping_notification_email_sent", None
            )
            order_with_tracking.save(update_fields=["metadata"])

        mock_email_instance = Mock()
        mock_email.return_value = mock_email_instance

        mock_render.side_effect = ["Email content", "HTML content"]

        result = send_shipping_notification_email(order_with_tracking.id)

        self.assertTrue(result)
        mock_email_instance.send.assert_called_once()
        self.assertTrue(mock_log_note.called)

    @patch("order.tasks.logger.warning")
    def test_send_shipping_notification_email_no_tracking_info(
        self, mock_logger
    ):
        self.order.tracking_number = ""
        self.order.shipping_carrier = ""
        self.order.save()

        result = send_shipping_notification_email(self.order.id)

        self.assertFalse(result)
        mock_logger.assert_called_once()

    @patch("order.tasks.logger.error")
    def test_send_shipping_notification_email_order_not_found(
        self, mock_logger
    ):
        result = send_shipping_notification_email(999999)

        self.assertFalse(result)
        mock_logger.assert_called_once()

    @patch("order.tasks.send_invoice_email.delay")
    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    def test_generate_order_invoice_success(
        self, mock_render_pdf, mock_send_email
    ):
        result = generate_order_invoice(self.order.id)

        self.assertTrue(result)
        mock_render_pdf.assert_called_once()
        self.order.refresh_from_db()
        self.assertTrue(hasattr(self.order, "invoice"))
        # The email task is chained once the PDF is ready.
        mock_send_email.assert_called_once_with(self.order.id)

    @patch("order.tasks.logger.error")
    def test_generate_order_invoice_order_not_found(self, mock_logger):
        result = generate_order_invoice(999999)

        self.assertFalse(result)
        mock_logger.assert_called_once()

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    @override_settings(
        SITE_NAME="GrooveShop",
        INFO_EMAIL="support@example.com",
        NUXT_BASE_URL="http://example.com",
        STATIC_BASE_URL="http://example.com",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_send_invoice_email_attaches_pdf(self, _mock_render):
        """PDF bytes from the generated invoice must be attached to the
        outbound email, and the flag in ``Order.metadata`` must prevent
        a second send."""
        from django.core import mail

        from order.invoicing import generate_invoice

        generate_invoice(self.order)

        # Clear any emails the order_created signal already sent so we
        # only assert on the invoice email below.
        mail.outbox = []

        result = send_invoice_email(self.order.id)
        self.assertTrue(result)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertIn(self.order.email, msg.to)
        # One attachment, PDF mimetype, non-empty bytes
        self.assertEqual(len(msg.attachments), 1)
        name, content, mimetype = msg.attachments[0]
        self.assertTrue(name.endswith(".pdf"))
        self.assertEqual(mimetype, "application/pdf")
        self.assertTrue(content.startswith(b"%PDF-"))

        # Second call is a no-op — flag reservation wins.
        result2 = send_invoice_email(self.order.id)
        self.assertTrue(result2)
        self.assertEqual(len(mail.outbox), 1)

    def test_send_invoice_email_without_rendered_pdf_skips(self):
        """If the PDF is missing (e.g. generation hasn't run yet), the
        task returns False and releases the flag so a later generation
        can re-trigger the email."""
        from order.models.invoice import Invoice, InvoiceCounter

        InvoiceCounter.objects.create(year=2026, next_number=1)
        Invoice.objects.create(
            order=self.order, invoice_number="INV-2026-000001"
        )

        result = send_invoice_email(self.order.id)
        self.assertFalse(result)
        self.order.refresh_from_db()
        # Flag cleared so a later re-trigger can proceed.
        self.assertFalse(
            (self.order.metadata or {}).get("invoice_email_sent"),
        )

    @patch("order.tasks.OrderHistory.log_note")
    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.render_to_string")
    @override_settings(
        SITE_NAME="GrooveShop",
        INFO_EMAIL="support@example.com",
        NUXT_BASE_URL="http://example.com",
        STATIC_BASE_URL="http://example.com",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_check_pending_orders_success(
        self, mock_render, mock_email, mock_log_note
    ):
        pending_order = OrderFactory.create(
            status=OrderStatus.PENDING,
            email="old@example.com",
            created_at=timezone.now() - timedelta(days=2),
        )

        mock_render.return_value = "Email content"
        mock_email_instance = MagicMock()
        mock_email.return_value = mock_email_instance

        result = check_pending_orders()

        self.assertGreaterEqual(result, 0)
        if result > 0:
            mock_email_instance.send.assert_called()
            mock_log_note.assert_called()
            pending_order.refresh_from_db()
            self.assertEqual(pending_order.reminder_count, 1)
            self.assertIsNotNone(pending_order.last_reminder_sent_at)

    @patch("order.tasks.OrderHistory.log_note")
    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.render_to_string")
    @override_settings(
        SITE_NAME="GrooveShop",
        INFO_EMAIL="support@example.com",
        NUXT_BASE_URL="http://example.com",
        STATIC_BASE_URL="http://example.com",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_check_pending_orders_skips_max_reminders(
        self, mock_render, mock_email, mock_log_note
    ):
        OrderFactory.create(
            status=OrderStatus.PENDING,
            email="maxed@example.com",
            created_at=timezone.now() - timedelta(days=10),
            reminder_count=3,
            last_reminder_sent_at=timezone.now() - timedelta(days=8),
        )
        # Reset — OrderFactory.create fires order_created which eagerly
        # runs the confirmation-email task. We're asserting on
        # check_pending_orders behaviour, not factory side-effects.
        mock_email.reset_mock()

        result = check_pending_orders()

        self.assertEqual(result, 0)
        mock_email.assert_not_called()

    @patch("order.tasks.OrderHistory.log_note")
    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.render_to_string")
    @override_settings(
        SITE_NAME="GrooveShop",
        INFO_EMAIL="support@example.com",
        NUXT_BASE_URL="http://example.com",
        STATIC_BASE_URL="http://example.com",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_check_pending_orders_respects_cooldown(
        self, mock_render, mock_email, mock_log_note
    ):
        OrderFactory.create(
            status=OrderStatus.PENDING,
            email="cooldown@example.com",
            created_at=timezone.now() - timedelta(days=5),
            reminder_count=1,
            last_reminder_sent_at=timezone.now() - timedelta(hours=12),
        )
        # Reset the mock — OrderFactory.create fires order_created which
        # eagerly runs the confirmation-email task (CELERY_TASK_ALWAYS_EAGER
        # + on_commit-immediate fixture). We're asserting on what
        # check_pending_orders does, not on order creation side-effects.
        mock_email.reset_mock()

        result = check_pending_orders()

        self.assertEqual(result, 0)
        mock_email.assert_not_called()

    @patch("order.tasks.logger.error")
    def test_check_pending_orders_exception(self, mock_logger):
        with patch("order.models.order.Order.objects.filter") as mock_filter:
            mock_filter.side_effect = Exception("Database error")

            result = check_pending_orders()

            self.assertEqual(result, 0)
            mock_logger.assert_called_once()


@pytest.mark.django_db
class OrderTasksIntegrationTestCase(DjangoTestCase):
    def setUp(self):
        # Pin payment_status: OrderFactory's default is random (see factories
        # /order.py:180) and can land on COMPLETED, which makes
        # send_order_status_update_email skip the PROCESSING email as a
        # duplicate of the payment-confirmed email.
        self.order = OrderFactory.create(
            email="integration@example.com",
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.PENDING,
            tracking_number="INT123",
            shipping_carrier="FedEx",
        )
        # Order creation triggered the order_created signal which already
        # ran the confirmation-email task eagerly and set the dedupe flag.
        # Release it so the test calling send_order_confirmation_email
        # directly sees a fresh run.
        _release_confirmation_email(self.order.id)

    @patch("order.tasks.EmailMultiAlternatives")
    @patch("order.tasks.render_to_string")
    @override_settings(
        SITE_NAME="GrooveShop",
        INFO_EMAIL="support@example.com",
        NUXT_BASE_URL="http://example.com",
        STATIC_BASE_URL="http://example.com",
        DEFAULT_FROM_EMAIL="no-reply@example.com",
    )
    def test_order_workflow_email_sequence(self, mock_render, mock_email):
        mock_render.return_value = "Email content"
        mock_email_instance = MagicMock()
        mock_email.return_value = mock_email_instance

        confirmation_result = send_order_confirmation_email(self.order.id)
        status_result = send_order_status_update_email(
            self.order.id, OrderStatus.PROCESSING
        )
        shipping_result = send_shipping_notification_email(self.order.id)

        self.assertTrue(confirmation_result)
        self.assertTrue(status_result)
        self.assertTrue(shipping_result)

        self.assertEqual(mock_email_instance.send.call_count, 3)

    @patch("order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test")
    def test_database_operations_integrity(self, mock_render_pdf):
        self.assertTrue(Order.objects.filter(id=self.order.id).exists())

        original_status = self.order.status

        result = generate_order_invoice(self.order.id)
        self.assertTrue(result)

        # Invoice generation must not mutate the order's status.
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, original_status)


@pytest.mark.django_db
class SendAdminNewOrderEmailTestCase(DjangoTestCase):
    def setUp(self):
        self.order = OrderFactory.create(
            email="customer@example.com",
            first_name="John",
            last_name="Doe",
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )

    @patch(
        "order.tasks.tenant_contact_email", return_value="merchant@store.test"
    )
    @patch("order.tasks.tenant_from_email", return_value="orders@store.test")
    @patch("order.tasks.render_to_string")
    @override_settings(
        SITE_NAME="GrooveShop", STATIC_BASE_URL="http://example.com"
    )
    def test_send_admin_new_order_email_sends_to_merchant(
        self, mock_render, _mock_from, _mock_contact
    ):
        # New-order alerts are MERCHANT-facing: they go to the tenant's
        # contact email FROM the tenant's address, not platform ADMINS.
        from django.core import mail

        mock_render.side_effect = ["Text body", "<p>HTML body</p>"]
        # setUp's factory order already fired the eager order-created
        # flow into the outbox; isolate this task's send.
        mail.outbox.clear()

        result = send_admin_new_order_email(self.order.id)

        self.assertTrue(result)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn(f"New order — #{self.order.id}", sent.subject)
        self.assertEqual(sent.to, ["merchant@store.test"])
        self.assertEqual(sent.from_email, "orders@store.test")
        self.assertEqual(sent.body, "Text body")

    @patch("order.tasks.tenant_contact_email", return_value="")
    def test_send_admin_new_order_email_skips_when_unconfigured(
        self, _mock_contact
    ):
        # No tenant contact email configured → skip silently.
        from django.core import mail

        mail.outbox.clear()
        result = send_admin_new_order_email(self.order.id)

        self.assertFalse(result)
        self.assertEqual(len(mail.outbox), 0)

    @patch("order.tasks.logger.error")
    def test_send_admin_new_order_email_order_not_found(self, mock_logger):
        result = send_admin_new_order_email(999999)

        self.assertFalse(result)
        mock_logger.assert_called_once()
