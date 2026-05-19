from unittest.mock import patch

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
        """Stub ``Setting.get`` so the validator reads our chosen value
        for ``B2B_INVOICING_ENABLED`` without touching the ``Setting``
        table.

        Earlier ``Setting.objects.update_or_create`` flaked under CI's
        parallel xdist run (the autouse ``_reseed_extra_settings``
        fixture in conftest.py rewrites the same ``EXTRA_SETTINGS_DEFAULTS``
        rows for every test on every worker, and the resulting savepoint-
        visibility interaction occasionally caused ``Setting.get`` to
        return the seeded default of ``True`` instead of the just-written
        ``False``). Patching the read site bypasses the round-trip
        entirely.

        The stub falls back to ``default`` for any non-B2B key so we
        don't reach for ``Setting.get.__func__`` — which can crash
        under pytest-xdist worker reuse if a prior test's MagicMock
        on ``Setting.get`` hasn't been fully unwound by the time this
        helper runs. The validator under test only consults
        ``B2B_INVOICING_ENABLED`` during ``is_valid``, so returning
        ``default`` for everything else is behaviourally equivalent to
        a real cache miss.
        """

        def stub(cls, key, default=None):
            if key == "B2B_INVOICING_ENABLED":
                return value
            return default

        p = patch.object(Setting, "get", classmethod(stub))
        p.start()
        self.addCleanup(p.stop)

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
