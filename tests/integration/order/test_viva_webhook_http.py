"""HTTP-level tests for the Viva Wallet webhook money path (G0400).

The payment-created (1796) webhook is a money path that previously had zero
test coverage. These POST to the real endpoint with ``_verify_transaction``
mocked, asserting a verified payment marks the order paid and an unverifiable
one does NOT.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory


class VivaWebhookMoneyPathTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse("viva-wallet-webhook")
        self.order = OrderFactory(
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            num_order_items=1,
            metadata={"viva_order_code": "OC123"},
        )
        # Multi-tenant scaffolding — same pattern as
        # tests/integration/tenant/test_multi_tenant_invariants.py: the
        # webhook resolves the owning tenant by iterating non-public
        # Tenant rows and entering each schema. Provision one row
        # (auto_create_schema=False) and no-op schema_context/
        # tenant_context so the lookup runs against the test's public
        # schema.
        from contextlib import contextmanager

        from tenant.models import Tenant

        tenant = Tenant(
            schema_name="viva_http_test",
            name="viva-http-test",
            slug="viva-http-test",
            owner_email="owner-viva-http-test@example.com",
            is_active=True,
            suspended_at=None,
        )
        tenant.auto_create_schema = False
        tenant.save()

        @contextmanager
        def _noop(_schema):
            yield

        @contextmanager
        def _noop_tenant(_tenant):
            yield

        for target in (
            "order.views.viva_webhook.schema_context",
            "django_tenants.utils.schema_context",
        ):
            patcher = patch(target, _noop)
            patcher.start()
            self.addCleanup(patcher.stop)

        # ``_handle_webhook_event`` now enters the resolved tenant via
        # ``tenant_context(tenant)`` (not ``schema_context(schema_name)``)
        # so ``connection.tenant`` is the real Tenant row — see
        # ``tenant.credentials`` no-fallback contract. Same no-op
        # treatment is needed here for the same reason.
        for target in (
            "order.views.viva_webhook.tenant_context",
            "django_tenants.utils.tenant_context",
        ):
            patcher = patch(target, _noop_tenant)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _post(self, body: dict):
        return self.client.post(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
        )

    def _event(self, **overrides):
        data = {
            "EventTypeId": 1796,
            "EventData": {
                "TransactionId": "viva-txn-1",
                "OrderCode": "OC123",
                "StatusId": "F",
            },
        }
        data["EventData"].update(overrides)
        return data

    def test_verified_payment_marks_order_paid(self):
        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(PaymentStatus.COMPLETED, {"order_code": "OC123"}),
        ):
            response = self._post(self._event())

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.COMPLETED)
        self.assertEqual(self.order.status, OrderStatus.PROCESSING)

    def test_unverifiable_transaction_does_not_mark_paid(self):
        # A spoofed 1796 whose transaction can't be verified must 500
        # (Viva retries) and never flip payment state.
        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(None, {}),
        ):
            response = self._post(self._event(TransactionId="forged"))

        self.assertEqual(response.status_code, 500)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.PENDING)
        self.assertEqual(self.order.status, OrderStatus.PENDING)

    def test_non_final_status_id_skips_payment(self):
        # StatusId != 'F' means the payment isn't finished — no state change.
        with patch(
            "order.views.viva_webhook._verify_transaction"
        ) as mock_verify:
            response = self._post(self._event(StatusId="E"))

        self.assertEqual(response.status_code, 200)
        mock_verify.assert_not_called()
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.PENDING)

    def test_unknown_order_code_is_acknowledged(self):
        response = self._post(self._event(OrderCode="does-not-exist"))
        # Acknowledged (200) so Viva stops retrying a webhook for an order
        # we don't have.
        self.assertEqual(response.status_code, 200)

    def test_payment_on_earlier_session_code_is_resolved(self):
        # Multi-session: the order was re-checked-out, so the singular
        # ``viva_order_code`` holds the LATEST code while an earlier
        # session's code survives only in ``viva_order_codes[]``. A
        # payment completed on the earlier session MUST still resolve
        # the order — previously the webhook matched only the latest
        # code, 200'd as "not found", and the payment was silently lost
        # (Viva treats a 200 as handled and never retries).
        self.order.metadata = {
            "viva_order_code": "OC_NEW",
            "viva_order_codes": ["OC_OLD", "OC_NEW"],
        }
        self.order.save(update_fields=["metadata"])

        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(PaymentStatus.COMPLETED, {"order_code": "OC_OLD"}),
        ):
            response = self._post(
                self._event(OrderCode="OC_OLD", TransactionId="viva-txn-old")
            )

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.COMPLETED)
        self.assertEqual(self.order.status, OrderStatus.PROCESSING)
