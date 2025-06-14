from django.test import TestCase
from djmoney.money import Money

from pay_way.models import PayWay


class PayWayModelTestCase(TestCase):
    def setUp(self):
        self.credit_card = PayWay.objects.create(
            active=True,
            cost=Money(0, "USD"),
            free_threshold=Money(100, "USD"),
            provider_code="stripe",
            is_online_payment=True,
            requires_confirmation=False,
            configuration={
                "public_key": "pk_test_123",
                "secret_key": "sk_test_123",
            },
        )
        self.credit_card.set_current_language("en")
        self.credit_card.name = "Credit Card"
        self.credit_card.description = "Pay with credit card"
        self.credit_card.instructions = "Enter your card details"
        self.credit_card.save()

        self.bank_transfer = PayWay.objects.create(
            active=True,
            cost=Money(0, "USD"),
            free_threshold=Money(0, "USD"),
            provider_code="",
            is_online_payment=False,
            requires_confirmation=True,
            sort_order=1,
        )
        self.bank_transfer.set_current_language("en")
        self.bank_transfer.name = "Bank Transfer"
        self.bank_transfer.description = "Pay via bank transfer"
        self.bank_transfer.instructions = "Transfer to Account: 123456789"
        self.bank_transfer.save()

        self.pay_on_delivery = PayWay.objects.create(
            active=True,
            cost=Money(5, "USD"),
            free_threshold=Money(50, "USD"),
            provider_code="",
            is_online_payment=False,
            requires_confirmation=False,
            sort_order=2,
        )
        self.pay_on_delivery.set_current_language("en")
        self.pay_on_delivery.name = "Pay On Delivery"
        self.pay_on_delivery.description = "Pay when your order is delivered"
        self.pay_on_delivery.save()

    def test_pay_way_str(self):
        self.assertEqual(str(self.credit_card), "Credit Card")
        self.assertEqual(str(self.bank_transfer), "Bank Transfer")
        self.assertEqual(str(self.pay_on_delivery), "Pay On Delivery")

    def test_pay_way_ordering(self):
        pay_ways = list(PayWay.objects.all())
        self.assertEqual(pay_ways[0], self.credit_card)
        self.assertEqual(pay_ways[1], self.bank_transfer)
        self.assertEqual(pay_ways[2], self.pay_on_delivery)

    def test_pay_way_translations(self):
        self.credit_card.set_current_language("en")
        self.assertEqual(self.credit_card.name, "Credit Card")
        self.assertEqual(self.credit_card.description, "Pay with credit card")

        self.bank_transfer.set_current_language("de")
        self.bank_transfer.name = "Banküberweisung"
        self.bank_transfer.save()

        self.bank_transfer.set_current_language("de")
        self.assertEqual(str(self.bank_transfer), "Banküberweisung")

        self.bank_transfer.set_current_language("en")
        self.assertEqual(str(self.bank_transfer), "Bank Transfer")

    def test_payment_configuration(self):
        self.assertEqual(
            self.credit_card.configuration,
            {"public_key": "pk_test_123", "secret_key": "sk_test_123"},
        )

    def test_payment_type_properties(self):
        self.assertTrue(self.credit_card.is_online_payment)
        self.assertFalse(self.credit_card.requires_confirmation)

        self.assertFalse(self.bank_transfer.is_online_payment)
        self.assertTrue(self.bank_transfer.requires_confirmation)

        self.assertFalse(self.pay_on_delivery.is_online_payment)
        self.assertFalse(self.pay_on_delivery.requires_confirmation)
