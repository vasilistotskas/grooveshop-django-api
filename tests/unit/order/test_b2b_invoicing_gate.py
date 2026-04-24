from django.test import TestCase
from extra_settings.models import Setting

from order.serializers.order import OrderCreateFromCartSerializer


BASE_PAYLOAD = {
    "pay_way_id": 1,
    "first_name": "Nikos",
    "last_name": "Papadopoulos",
    "email": "nikos@example.com",
    "street": "Ermou",
    "street_number": "10",
    "city": "Athens",
    "zipcode": "10563",
    "country_id": "GR",
    "phone": "+302101234567",
}


class B2BInvoicingGateTestCase(TestCase):
    """Regression: the ``B2B_INVOICING_ENABLED`` extra setting must be
    honoured by ``OrderCreateFromCartSerializer.validate()`` so the
    backend refuses INVOICE orders when the owner has disabled B2B via
    admin, closing the direct-API bypass that would otherwise defeat
    the UI gate in ``StepPersonalInfo.vue``.
    """

    def _set_enabled(self, value: bool) -> None:
        Setting.objects.update_or_create(
            name="B2B_INVOICING_ENABLED",
            defaults={"value_type": "bool", "value_bool": value},
        )

    def test_invoice_rejected_when_setting_disabled(self):
        self._set_enabled(False)
        serializer = OrderCreateFromCartSerializer(
            data={
                **BASE_PAYLOAD,
                "document_type": "INVOICE",
                "billing_vat_id": "123456789",
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("document_type", serializer.errors)

    def test_invoice_accepted_when_setting_enabled(self):
        self._set_enabled(True)
        serializer = OrderCreateFromCartSerializer(
            data={
                **BASE_PAYLOAD,
                "document_type": "INVOICE",
                "billing_vat_id": "123456789",
            }
        )
        # ΑΦΜ 123456789 is structurally valid (9 digits); pay_way_id
        # lookup happens downstream in the service, not the serializer.
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_receipt_accepted_regardless_of_setting(self):
        self._set_enabled(False)
        serializer = OrderCreateFromCartSerializer(
            data={**BASE_PAYLOAD, "document_type": "RECEIPT"}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
