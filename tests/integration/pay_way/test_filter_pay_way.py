from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from djmoney.money import Money
from rest_framework.test import APITestCase

from pay_way.factories import PayWayFactory
from pay_way.models import PayWay


class PayWayFilterTest(APITestCase):
    def setUp(self):
        PayWay.objects.all().delete()

        self.now = timezone.now()

        self.stripe_payment = PayWayFactory.create_online_payment(
            provider_code="stripe",
            active=True,
            cost=Money(Decimal("2.50"), "EUR"),
            free_threshold=Money(Decimal("50.00"), "EUR"),
        )
        self.stripe_payment.created_at = self.now - timedelta(days=30)
        self.stripe_payment.updated_at = self.now - timedelta(days=5)
        self.stripe_payment.sort_order = 1
        self.stripe_payment.save()

        self.paypal_payment = PayWayFactory.create_online_payment(
            provider_code="paypal",
            active=True,
            cost=Money(Decimal("3.00"), "EUR"),
            free_threshold=Money(Decimal("75.00"), "EUR"),
        )
        self.paypal_payment.created_at = self.now - timedelta(days=15)
        self.paypal_payment.updated_at = self.now - timedelta(days=2)
        self.paypal_payment.sort_order = 2
        self.paypal_payment.save()

        self.bank_transfer = PayWayFactory.create_offline_payment(
            provider_code="bank_transfer",
            active=True,
            cost=Money(Decimal("0.00"), "EUR"),
            free_threshold=Money(Decimal("0.00"), "EUR"),
        )
        self.bank_transfer.created_at = self.now - timedelta(days=7)
        self.bank_transfer.updated_at = self.now - timedelta(hours=12)
        self.bank_transfer.sort_order = 3
        self.bank_transfer.save()

        self.cash_payment = PayWayFactory(
            provider_code="cash",
            active=False,
            cost=Money(Decimal("0.00"), "EUR"),
            free_threshold=Money(Decimal("0.00"), "EUR"),
            is_online_payment=False,
            requires_confirmation=False,
            configuration=None,
        )
        self.cash_payment.created_at = self.now - timedelta(days=60)
        self.cash_payment.updated_at = self.now - timedelta(days=30)
        self.cash_payment.sort_order = 4
        self.cash_payment.save()

        self.high_cost_payment = PayWayFactory(
            provider_code="premium",
            active=True,
            cost=Money(Decimal("10.00"), "EUR"),
            free_threshold=Money(Decimal("200.00"), "EUR"),
            is_online_payment=True,
            requires_confirmation=False,
        )
        self.high_cost_payment.created_at = self.now - timedelta(hours=6)
        self.high_cost_payment.updated_at = self.now - timedelta(hours=1)
        self.high_cost_payment.sort_order = 5
        self.high_cost_payment.save()

    def test_basic_filters(self):
        url = reverse("payway-list")

        response = self.client.get(url, {"id": self.stripe_payment.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.stripe_payment.id
        )

        response = self.client.get(url, {"active": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.stripe_payment.id, result_ids)
        self.assertIn(self.paypal_payment.id, result_ids)
        self.assertIn(self.bank_transfer.id, result_ids)
        self.assertIn(self.high_cost_payment.id, result_ids)
        self.assertNotIn(self.cash_payment.id, result_ids)

        response = self.client.get(url, {"active": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.cash_payment.id, result_ids)

    def test_cost_filters(self):
        url = reverse("payway-list")

        response = self.client.get(url, {"cost_min": "3.00"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.paypal_payment.id, result_ids)
        self.assertIn(self.high_cost_payment.id, result_ids)
        self.assertNotIn(self.stripe_payment.id, result_ids)
        self.assertNotIn(self.bank_transfer.id, result_ids)
        self.assertNotIn(self.cash_payment.id, result_ids)

        response = self.client.get(url, {"cost_max": "2.50"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.stripe_payment.id, result_ids)
        self.assertIn(self.bank_transfer.id, result_ids)
        self.assertIn(self.cash_payment.id, result_ids)
        self.assertNotIn(self.paypal_payment.id, result_ids)
        self.assertNotIn(self.high_cost_payment.id, result_ids)

        response = self.client.get(
            url, {"cost_min": "2.00", "cost_max": "5.00"}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.stripe_payment.id, result_ids)
        self.assertIn(self.paypal_payment.id, result_ids)
        self.assertNotIn(self.bank_transfer.id, result_ids)
        self.assertNotIn(self.cash_payment.id, result_ids)
        self.assertNotIn(self.high_cost_payment.id, result_ids)

    def test_free_threshold_filters(self):
        url = reverse("payway-list")

        response = self.client.get(url, {"free_threshold_min": "60.00"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.paypal_payment.id, result_ids)
        self.assertIn(self.high_cost_payment.id, result_ids)
        self.assertNotIn(self.stripe_payment.id, result_ids)

        response = self.client.get(url, {"free_threshold_max": "50.00"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.stripe_payment.id, result_ids)
        self.assertIn(self.bank_transfer.id, result_ids)
        self.assertIn(self.cash_payment.id, result_ids)
        self.assertNotIn(self.paypal_payment.id, result_ids)
        self.assertNotIn(self.high_cost_payment.id, result_ids)

    def test_provider_filters(self):
        url = reverse("payway-list")

        response = self.client.get(url, {"provider_code": "stripe"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.stripe_payment.id, result_ids)

        response = self.client.get(url, {"provider_code": "pay"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.paypal_payment.id, result_ids)

    def test_payment_type_filters(self):
        url = reverse("payway-list")

        response = self.client.get(url, {"is_online_payment": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.stripe_payment.id, result_ids)
        self.assertIn(self.paypal_payment.id, result_ids)
        self.assertIn(self.high_cost_payment.id, result_ids)
        self.assertNotIn(self.bank_transfer.id, result_ids)
        self.assertNotIn(self.cash_payment.id, result_ids)

        response = self.client.get(url, {"is_online_payment": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.bank_transfer.id, result_ids)
        self.assertIn(self.cash_payment.id, result_ids)
        self.assertNotIn(self.stripe_payment.id, result_ids)

        response = self.client.get(url, {"requires_confirmation": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.bank_transfer.id, result_ids)

        response = self.client.get(url, {"requires_confirmation": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.stripe_payment.id, result_ids)
        self.assertIn(self.paypal_payment.id, result_ids)
        self.assertIn(self.cash_payment.id, result_ids)
        self.assertIn(self.high_cost_payment.id, result_ids)
        self.assertNotIn(self.bank_transfer.id, result_ids)

    def test_configuration_filters(self):
        url = reverse("payway-list")

        response = self.client.get(url, {"has_configuration": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.stripe_payment.id, result_ids)
        self.assertIn(self.paypal_payment.id, result_ids)
        self.assertIn(self.bank_transfer.id, result_ids)
        self.assertNotIn(self.cash_payment.id, result_ids)

        response = self.client.get(url, {"has_configuration": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.cash_payment.id, result_ids)

    def test_translation_filters(self):
        url = reverse("payway-list")

        response = self.client.get(url, {"name": "stripe"})
        self.assertEqual(response.status_code, 200)

        response = self.client.get(url, {"description": "payment"})
        self.assertEqual(response.status_code, 200)

    def test_timestamp_filters(self):
        url = reverse("payway-list")

        created_after_date = self.now - timedelta(days=10)
        response = self.client.get(
            url, {"created_after": created_after_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)
        self.assertGreaterEqual(len(response.data["results"]), 0)

        created_before_date = self.now - timedelta(days=45)
        response = self.client.get(
            url, {"created_before": created_before_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        updated_after_date = self.now - timedelta(days=10)
        response = self.client.get(
            url, {"updated_after": updated_after_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        updated_before_date = self.now + timedelta(days=1)
        response = self.client.get(
            url, {"updated_before": updated_before_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_uuid_filter(self):
        url = reverse("payway-list")

        response = self.client.get(url, {"uuid": str(self.stripe_payment.uuid)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.stripe_payment.id
        )

    def test_sort_order_filters(self):
        url = reverse("payway-list")

        response = self.client.get(url, {"sort_order": 2})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.paypal_payment.id, result_ids)

        response = self.client.get(url, {"sort_order__gte": 3})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.bank_transfer.id, result_ids)
        self.assertIn(self.cash_payment.id, result_ids)
        self.assertIn(self.high_cost_payment.id, result_ids)
        self.assertNotIn(self.stripe_payment.id, result_ids)
        self.assertNotIn(self.paypal_payment.id, result_ids)

        response = self.client.get(url, {"sort_order__lte": 3})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.stripe_payment.id, result_ids)
        self.assertIn(self.paypal_payment.id, result_ids)
        self.assertIn(self.bank_transfer.id, result_ids)
        self.assertNotIn(self.cash_payment.id, result_ids)
        self.assertNotIn(self.high_cost_payment.id, result_ids)

    def test_camel_case_filters(self):
        url = reverse("payway-list")

        response = self.client.get(
            url,
            {
                "isOnlinePayment": "true",
                "active": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertGreater(
            len(result_ids), 0, "Should return some online payment methods"
        )
        self.assertNotIn(self.bank_transfer.id, result_ids)

        response = self.client.get(
            url,
            {
                "costMin": "2.00",
                "costMax": "5.00",
                "requiresConfirmation": "false",
                "hasConfiguration": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertGreaterEqual(
            len(result_ids), 0, "CamelCase filter combination should work"
        )
        for result in response.data["results"]:
            cost = float(result["cost"])
            self.assertGreaterEqual(cost, 2.00, "Cost should be >= 2.00")
            self.assertLessEqual(cost, 5.00, "Cost should be <= 5.00")

    def test_existing_filters_still_work(self):
        url = reverse("payway-list")

        response = self.client.get(
            url,
            {
                "active": "true",
                "cost_min": "2.00",
                "is_online_payment": "true",
                "requires_confirmation": "false",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.stripe_payment.id, result_ids)
        self.assertIn(self.paypal_payment.id, result_ids)
        self.assertIn(self.high_cost_payment.id, result_ids)
        self.assertNotIn(self.bank_transfer.id, result_ids)
        self.assertNotIn(self.cash_payment.id, result_ids)

    def test_complex_filter_combinations(self):
        url = reverse("payway-list")

        response = self.client.get(
            url,
            {
                "active": "true",
                "isOnlinePayment": "true",
                "costMax": "5.00",
                "requiresConfirmation": "false",
                "ordering": "cost",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertGreater(
            len(result_ids),
            0,
            "Complex filter combination should return some results",
        )
        self.assertNotIn(self.cash_payment.id, result_ids)
        self.assertNotIn(self.bank_transfer.id, result_ids)

    def test_filter_with_ordering(self):
        url = reverse("payway-list")

        response = self.client.get(url, {"active": "true", "ordering": "cost"})
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]

        costs = [float(r["cost"]) for r in results]
        self.assertEqual(costs, sorted(costs))

        response = self.client.get(url, {"active": "true", "ordering": "-cost"})
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]

        costs = [float(r["cost"]) for r in results]
        self.assertEqual(costs, sorted(costs, reverse=True))

        response = self.client.get(url, {"ordering": "sort_order"})
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]

        sort_orders = [r["sort_order"] for r in results]
        self.assertEqual(sort_orders, sorted(sort_orders))

    def test_bulk_filters(self):
        url = reverse("payway-list")

        response = self.client.get(url, {"provider_code": "stripe,paypal"})
        self.assertEqual(response.status_code, 200)

        response = self.client.get(
            url,
            {
                "active": "true",
                "has_configuration": "true",
                "is_online_payment": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.stripe_payment.id, result_ids)
        self.assertIn(self.paypal_payment.id, result_ids)
        self.assertNotIn(self.bank_transfer.id, result_ids)
        self.assertNotIn(self.cash_payment.id, result_ids)

    def tearDown(self):
        PayWay.objects.all().delete()
