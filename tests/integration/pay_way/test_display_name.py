"""A pay way must not print its own enum key at a human.

``PayWayTranslation.name`` stores a ``PayWayEnum`` KEY on purpose — one
shared vocabulary across the two repos, seedable by a migration without
knowing a language. Nothing resolved it, so every Django-rendered
surface printed the key: the invoice PDF's "Method" line on a Greek tax
document, the merchant's new-order email, the admin list, and every
autocomplete label.

``display_name`` is that resolver, and these tests pin the two halves
that matter: a known key becomes its localised label, and an unknown
one is passed through rather than swallowed (Django's documented
``get_FOO_display()`` contract).
"""

from __future__ import annotations

from django.core.cache import cache
from django.test import TestCase
from django.utils import translation

from order.factories.order import OrderFactory
from order.invoicing import _pay_way_display
from pay_way.enum.pay_way import PayWayEnum
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from pay_way.models import PayWay


def _set_name(pay_way, name: str):
    """Write the name token and hand back an instance that can see it.

    ``.update()`` writes straight to SQL, past BOTH of parler's caches:
    the per-instance ``_translations_cache`` and the shared cache
    backend that ``PARLER_ENABLE_CACHING`` populates on first read. A
    plain re-fetch clears only the first, so without the explicit
    ``cache.clear()`` every assertion here reads whichever name the
    factory's ``Iterator`` happened to assign and the test lies.
    """
    pay_way.translations.update(name=name)
    cache.clear()
    return PayWay.objects.get(pk=pay_way.pk)


class DisplayNameTests(TestCase):
    def setUp(self):
        self.pay_way = PayWayFactory(
            active=True,
            provider_code="cash_on_delivery",
            settlement=PaySettlement.COURIER_CASH.value,
        )

    def test_a_known_key_resolves_to_its_label(self):
        self.pay_way = _set_name(self.pay_way, PayWayEnum.PAY_ON_DELIVERY.value)

        with translation.override("en"):
            self.assertEqual(self.pay_way.display_name, "Pay On Delivery")

    def test_it_returns_the_label_not_the_stored_key(self):
        """The whole point: the column holds a KEY, humans need a LABEL.

        Asserts against ``PayWayEnum.…label`` rather than a literal
        Greek string. The labels are ``gettext_lazy``, so ``str()``
        resolves them under whatever language is active — and CI has no
        ``compilemessages`` step, so every catalog there falls back to
        the English source while a dev machine has the compiled ``el``
        ``.mo`` and returns Greek. An earlier version of this test
        asserted the Greek wording and so passed locally and failed in
        CI; ``tests/conftest.py::_assert_english_locale_if_marked``
        documents the same trap in the opposite direction.

        What matters is environment-independent: the resolved label is
        never the raw key.
        """
        self.pay_way = _set_name(self.pay_way, PayWayEnum.PAY_ON_DELIVERY.value)

        with translation.override("el"):
            resolved = self.pay_way.display_name
            self.assertEqual(resolved, str(PayWayEnum.PAY_ON_DELIVERY.label))

        self.assertNotEqual(resolved, PayWayEnum.PAY_ON_DELIVERY.value)

    def test_the_brand_name_is_not_translated(self):
        """BOX NOW requires its product name shown verbatim.

        It carries no Greek msgstr on purpose, so gettext returns the
        msgid — which is the desired output, not a missing translation.
        """
        self.pay_way = _set_name(
            self.pay_way, PayWayEnum.BOX_NOW_PAY_ON_THE_GO.value
        )

        with translation.override("el"):
            self.assertEqual(
                self.pay_way.display_name, "BOX NOW PAY ON THE GO!"
            )

    def test_an_unknown_value_is_passed_through(self):
        """Mirrors ``get_FOO_display()``: unknown values are returned
        as-is rather than blanked, so an operator's hand-edited row
        still shows something."""
        self.pay_way = _set_name(self.pay_way, "SOMETHING_ELSE")

        self.assertEqual(self.pay_way.display_name, "SOMETHING_ELSE")

    def test_blank_stays_blank_so_callers_can_fall_back(self):
        self.pay_way = _set_name(self.pay_way, "")

        self.assertEqual(self.pay_way.display_name, "")

    def test_str_uses_it(self):
        """``__str__`` drives every admin autocomplete label."""
        self.pay_way = _set_name(self.pay_way, PayWayEnum.PAY_ON_DELIVERY.value)

        with translation.override("en"):
            self.assertEqual(str(self.pay_way), "Pay On Delivery")


class InvoiceLabelTests(TestCase):
    """The invoice PDF is a legal document; it printed the raw key."""

    def setUp(self):
        self.pay_way = PayWayFactory(
            active=True,
            provider_code="cash_on_delivery",
            settlement=PaySettlement.COURIER_CASH.value,
        )
        self.pay_way = _set_name(self.pay_way, PayWayEnum.PAY_ON_DELIVERY.value)

    def test_prefers_the_resolved_label(self):
        order = OrderFactory(pay_way=self.pay_way, payment_method="acs_cod")

        with translation.override("en"):
            self.assertEqual(_pay_way_display(order), "Pay On Delivery")

    def test_falls_back_to_the_snapshot_when_the_pay_way_is_gone(self):
        """``Order.pay_way`` is ``SET_NULL``. Before the snapshot the
        invoice fell straight through to the gateway code and printed
        "acs_cod" as the payment method."""
        order = OrderFactory(pay_way=self.pay_way, payment_method="acs_cod")
        self.pay_way.delete()
        order.refresh_from_db()

        self.assertIsNone(order.pay_way)
        with translation.override("en"):
            self.assertEqual(_pay_way_display(order), "Pay On Delivery")

    def test_falls_back_to_the_gateway_code_as_a_last_resort(self):
        order = OrderFactory(pay_way=None, payment_method="acs_cod")
        order.pay_way_key = ""
        order.save(update_fields=["pay_way_key"])

        self.assertEqual(_pay_way_display(order), "acs_cod")

    def test_returns_empty_rather_than_none(self):
        order = OrderFactory(pay_way=None, payment_method="")
        order.pay_way_key = ""
        order.save(update_fields=["pay_way_key"])

        self.assertEqual(_pay_way_display(order), "")
