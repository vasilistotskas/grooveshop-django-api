from django.conf import settings
from django.test import TransactionTestCase
from djmoney.money import Money

from pay_way.enum.settlement import PaySettlement
from pay_way.models import PayWay


class PayWayModelTestCase(TransactionTestCase):
    def setUp(self):
        self.credit_card = PayWay.objects.create(
            active=True,
            cost=Money(0, settings.DEFAULT_CURRENCY),
            free_threshold=Money(100, settings.DEFAULT_CURRENCY),
            provider_code="stripe",
            settlement=PaySettlement.ONLINE,
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
            cost=Money(0, settings.DEFAULT_CURRENCY),
            free_threshold=Money(0, settings.DEFAULT_CURRENCY),
            provider_code="",
            settlement=PaySettlement.OFFLINE_TRANSFER,
            sort_order=1,
        )
        self.bank_transfer.set_current_language("en")
        self.bank_transfer.name = "Bank Transfer"
        self.bank_transfer.description = "Pay via bank transfer"
        self.bank_transfer.instructions = "Transfer to Account: 123456789"
        self.bank_transfer.save()

        self.pay_on_delivery = PayWay.objects.create(
            active=True,
            cost=Money(5, settings.DEFAULT_CURRENCY),
            free_threshold=Money(50, settings.DEFAULT_CURRENCY),
            provider_code="",
            settlement=PaySettlement.COURIER_CASH,
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

    def test_settlement_is_the_stored_truth(self):
        self.assertEqual(self.credit_card.settlement, PaySettlement.ONLINE)
        self.assertEqual(
            self.bank_transfer.settlement, PaySettlement.OFFLINE_TRANSFER
        )
        self.assertEqual(
            self.pay_on_delivery.settlement, PaySettlement.COURIER_CASH
        )

    def test_deprecated_booleans_track_settlement(self):
        # The two columns survive for one release (expand/contract) and
        # ``save()`` derives them, so a row can never disagree with
        # itself while the previous pods still read them. Delete this
        # test with the columns.
        self.assertTrue(self.credit_card.is_online_payment)
        self.assertFalse(self.credit_card.requires_confirmation)

        self.assertFalse(self.bank_transfer.is_online_payment)
        self.assertTrue(self.bank_transfer.requires_confirmation)

        self.assertFalse(self.pay_on_delivery.is_online_payment)
        self.assertFalse(self.pay_on_delivery.requires_confirmation)

    def test_deprecated_booleans_cannot_be_written_directly(self):
        # Writing the mirror must not create a row that contradicts its
        # settlement — that divergence is what let a courier-COD
        # pay-way mint a BoxNow locker voucher.
        self.pay_on_delivery.is_online_payment = True
        self.pay_on_delivery.save()
        self.pay_on_delivery.refresh_from_db()

        self.assertFalse(self.pay_on_delivery.is_online_payment)
        self.assertEqual(
            self.pay_on_delivery.settlement, PaySettlement.COURIER_CASH
        )

    def test_is_collected_on_delivery_spans_both_products(self):
        # Both COD-shaped settlements need a non-zero voucher amount,
        # and this property must NOT distinguish them — that is what
        # ``settlement`` is for.
        self.assertTrue(self.pay_on_delivery.is_collected_on_delivery)
        self.assertFalse(self.credit_card.is_collected_on_delivery)
        self.assertFalse(self.bank_transfer.is_collected_on_delivery)

        terminal = PayWay.objects.create(
            active=True,
            provider_code="boxnow_pay_on_the_go",
            settlement=PaySettlement.CARRIER_TERMINAL,
            sort_order=3,
        )
        self.assertTrue(terminal.is_collected_on_delivery)
