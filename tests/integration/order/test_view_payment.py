from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import TestCase
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from order.enum.status import PaymentStatus
from order.factories.order import OrderFactory
from order.views.payment import OrderPaymentViewSet
from pay_way.factories import PayWayFactory

User = get_user_model()


def get_response(request):
    return None


def add_session_to_request(request):
    middleware = SessionMiddleware(get_response)
    middleware.process_request(request)
    request.session.save()
    return request


class OrderPaymentViewSetTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.factory = APIRequestFactory()

        self.admin_user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )

        self.user = User.objects.create_user(
            username="user", email="user@example.com", password="password"
        )

        self.stripe_pay_way = PayWayFactory(
            active=True,
            provider_code="stripe",
            is_online_payment=True,
            requires_confirmation=False,
        )

        self.bank_transfer_pay_way = PayWayFactory(
            active=True,
            provider_code="",
            is_online_payment=False,
            requires_confirmation=True,
        )

        self.user_order = OrderFactory(
            user=self.user,
            pay_way=None,
            payment_status=PaymentStatus.PENDING,
            payment_id="",
            payment_method="",
            status="PENDING",
            paid_amount=Money(0, "USD"),
        )

        self.anon_order = OrderFactory(
            user=None,
            pay_way=None,
            payment_status=PaymentStatus.PENDING,
            payment_id="",
            payment_method="",
            status="PENDING",
            paid_amount=Money(0, "USD"),
        )

        self.paid_order = OrderFactory(
            user=self.user,
            pay_way=self.stripe_pay_way,
            payment_status=PaymentStatus.COMPLETED,
            payment_id="PAID_123",
            payment_method="Stripe",
            status="COMPLETED",
        )
        self.paid_order.paid_amount = self.paid_order.total_price
        self.paid_order.save()

    @mock.patch(
        "order.models.order.Order.is_paid", new_callable=mock.PropertyMock
    )
    def test_process_payment_authenticated_user(self, mock_is_paid):
        mock_is_paid.return_value = False

        self.client.force_authenticate(user=self.user)

        view = OrderPaymentViewSet.as_view({"post": "process_payment"})
        request = self.factory.post(
            f"/api/v1/order/{self.user_order.pk}/process_payment",
            {
                "pay_way_id": self.stripe_pay_way.id,
                "payment_data": {"token": "test_token"},
            },
            format="json",
        )
        force_authenticate(request, user=self.user)

        with mock.patch(
            "order.views.payment.PayWayService.process_payment"
        ) as mock_process_payment:
            mock_process_payment.return_value = (
                True,
                {
                    "payment_id": "TEST_PAYMENT_123",
                    "status": PaymentStatus.COMPLETED,
                    "amount": "100.00",
                    "currency": "USD",
                    "provider": "stripe",
                },
            )

            response = view(request, pk=self.user_order.pk)

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data["order_id"], self.user_order.id)
            self.assertIn("payment_status", response.data)
            self.assertIn("payment_id", response.data)

    @mock.patch(
        "order.models.order.Order.is_paid", new_callable=mock.PropertyMock
    )
    def test_process_payment_anonymous_user(self, mock_is_paid):
        mock_is_paid.return_value = False

        request = self.factory.post(
            f"/api/v1/order/{self.anon_order.pk}/process_payment",
            {"pay_way_id": self.bank_transfer_pay_way.id, "payment_data": {}},
            format="json",
        )

        add_session_to_request(request)

        view = OrderPaymentViewSet.as_view({"post": "process_payment"})

        with mock.patch(
            "order.views.payment.PayWayService.process_payment"
        ) as mock_process_payment:
            mock_process_payment.return_value = (
                True,
                {
                    "payment_id": f"OFFLINE_{self.anon_order.id}",
                    "status": PaymentStatus.PENDING,
                    "amount": "100.00",
                    "currency": "USD",
                    "provider": "offline",
                },
            )

            response = view(request, pk=self.anon_order.pk)

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            mock_process_payment.assert_called_once()

            self.assertTrue(
                request.session.get(f"order_{self.anon_order.uuid}")
            )

    @mock.patch(
        "order.models.order.Order.is_paid", new_callable=mock.PropertyMock
    )
    def test_process_payment_unauthorized_user(self, mock_is_paid):
        mock_is_paid.return_value = False

        regular_user = User.objects.create_user(
            username="another_user",
            email="another@example.com",
            password="password",
        )

        view = OrderPaymentViewSet.as_view({"post": "process_payment"})
        request = self.factory.post(
            f"/api/v1/order/{self.user_order.pk}/process_payment",
            {
                "pay_way_id": self.stripe_pay_way.id,
                "payment_data": {"token": "test_token"},
            },
            format="json",
        )
        force_authenticate(request, user=regular_user)
        response = view(request, pk=self.user_order.pk)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        request = self.factory.post(
            f"/api/v1/order/{self.user_order.pk}/process_payment",
            {
                "pay_way_id": self.stripe_pay_way.id,
                "payment_data": {"token": "test_token"},
            },
            format="json",
        )
        force_authenticate(request, user=self.admin_user)

        with mock.patch(
            "order.views.payment.PayWayService.process_payment"
        ) as mock_process:
            mock_process.return_value = (True, {"payment_id": "test"})
            response = view(request, pk=self.user_order.pk)
            self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_process_payment_already_paid(self):
        view = OrderPaymentViewSet.as_view({"post": "process_payment"})
        request = self.factory.post(
            f"/api/v1/order/{self.paid_order.pk}/process_payment",
            {
                "pay_way_id": self.stripe_pay_way.id,
                "payment_data": {"token": "test_token"},
            },
            format="json",
        )
        force_authenticate(request, user=self.user)
        response = view(request, pk=self.paid_order.pk)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already been paid", response.data["detail"])

    @mock.patch("order.views.payment.PayWayService.check_payment_status")
    def test_check_payment_status(self, mock_check_status):
        mock_check_status.return_value = (
            PaymentStatus.COMPLETED,
            {
                "payment_id": "TEST_PAYMENT_123",
                "raw_status": "succeeded",
                "provider": "stripe",
            },
        )

        order = self.user_order
        order.payment_id = "TEST_PAYMENT_123"
        order.pay_way = self.stripe_pay_way
        order.save()

        view = OrderPaymentViewSet.as_view({"get": "check_payment_status"})
        request = self.factory.get(f"/api/v1/order/{order.pk}/payment_status")
        force_authenticate(request, user=self.user)
        response = view(request, pk=order.pk)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_check_status.assert_called_once()

        self.assertEqual(response.data["order_id"], order.id)
        self.assertEqual(
            response.data["payment_status"], PaymentStatus.COMPLETED
        )

    def test_check_payment_status_no_payment(self):
        view = OrderPaymentViewSet.as_view({"get": "check_payment_status"})
        request = self.factory.get(
            f"/api/v1/order/{self.user_order.pk}/payment_status"
        )
        force_authenticate(request, user=self.user)
        response = view(request, pk=self.user_order.pk)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("no payment method", response.data["detail"])

        self.user_order.pay_way = self.stripe_pay_way
        self.user_order.save()

        request = self.factory.get(
            f"/api/v1/order/{self.user_order.pk}/payment_status"
        )
        force_authenticate(request, user=self.user)
        response = view(request, pk=self.user_order.pk)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("no associated payment", response.data["detail"])

    @mock.patch("order.views.payment.PayWayService.refund_payment")
    @mock.patch(
        "order.models.order.Order.is_paid", new_callable=mock.PropertyMock
    )
    def test_refund_payment_admin_user(self, mock_is_paid, mock_refund):
        mock_is_paid.return_value = True

        self.paid_order.refresh_from_db()
        if not self.paid_order.pay_way:
            self.paid_order.pay_way = self.stripe_pay_way
            self.paid_order.save()

        mock_refund.return_value = (
            True,
            {
                "refund_id": "REFUND_123",
                "status": PaymentStatus.REFUNDED,
                "amount": "full refund",
                "payment_id": "PAID_123",
            },
        )

        view = OrderPaymentViewSet.as_view({"post": "refund_payment"})
        request = self.factory.post(
            f"/api/v1/order/{self.paid_order.pk}/refund"
        )
        force_authenticate(request, user=self.admin_user)
        response = view(request, pk=self.paid_order.pk)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_refund.assert_called_once()

        self.assertEqual(response.data["order_id"], self.paid_order.id)
        self.assertIn("payment_status", response.data)
        self.assertEqual(response.data["refund_id"], "REFUND_123")
        self.assertIn("refund_details", response.data)

    def test_refund_payment_non_admin_user(self):
        self.paid_order.refresh_from_db()
        if not self.paid_order.pay_way:
            self.paid_order.pay_way = self.stripe_pay_way
            self.paid_order.save()

        view = OrderPaymentViewSet.as_view({"post": "refund_payment"})
        request = self.factory.post(
            f"/api/v1/order/{self.paid_order.pk}/refund"
        )
        force_authenticate(request, user=self.user)
        response = view(request, pk=self.paid_order.pk)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @mock.patch("order.views.payment.PayWayService.refund_payment")
    @mock.patch(
        "order.models.order.Order.is_paid", new_callable=mock.PropertyMock
    )
    def test_refund_payment_partial(self, mock_is_paid, mock_refund):
        mock_is_paid.return_value = True

        self.paid_order.refresh_from_db()
        if not self.paid_order.pay_way:
            self.paid_order.pay_way = self.stripe_pay_way
            self.paid_order.save()

        mock_refund.return_value = (
            True,
            {
                "refund_id": "REFUND_123",
                "status": PaymentStatus.PARTIALLY_REFUNDED,
                "amount": "50.00",
                "currency": "USD",
                "payment_id": "PAID_123",
            },
        )

        view = OrderPaymentViewSet.as_view({"post": "refund_payment"})
        request = self.factory.post(
            f"/api/v1/order/{self.paid_order.pk}/refund",
            {"amount": 50.00, "currency": "USD"},
            format="json",
        )
        force_authenticate(request, user=self.admin_user)
        response = view(request, pk=self.paid_order.pk)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        amount_arg = mock_refund.call_args[1]["amount"]
        self.assertEqual(amount_arg.amount, 50.00)
        self.assertEqual(str(amount_arg.currency), "USD")
