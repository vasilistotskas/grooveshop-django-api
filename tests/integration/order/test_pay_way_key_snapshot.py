"""What the customer chose must outlive the row they chose it from.

``Order.pay_way`` is ``on_delete=SET_NULL``, so deleting a PayWay
erases the only record of the payment method from every order that used
it — and the storefront then falls back to ``Order.payment_method``,
which is the GATEWAY code (``acs_cod``, ``viva_wallet``), not the
shopper's choice. Renaming a key has the subtler version of the same
problem: ``pay_way`` migration 0022 relabelled a live row, and without a
snapshot every historical order silently changed its story.

Written in ``Order.save()`` rather than at the order-creation call
sites, because there are several (cart checkout, the agent/UCP surface,
reorder) and a new one must not be able to forget — the same reasoning
that put ``updated_at`` there.
"""

from __future__ import annotations

from django.core.cache import cache
from django.test import TestCase

from order.factories.order import OrderFactory
from order.models.order import Order
from pay_way.enum.pay_way import PayWayEnum
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from pay_way.models import PayWay


def _pay_way(name: str, **kwargs) -> PayWay:
    kwargs.setdefault("active", True)
    kwargs.setdefault("settlement", PaySettlement.COURIER_CASH.value)
    pay_way = PayWayFactory(**kwargs)
    pay_way.translations.update(name=name)
    cache.clear()
    return PayWay.objects.get(pk=pay_way.pk)


class SnapshotOnWriteTests(TestCase):
    def setUp(self):
        self.courier_cash = _pay_way(PayWayEnum.PAY_ON_DELIVERY.value)
        self.card = _pay_way(
            PayWayEnum.CREDIT_CARD.value,
            settlement=PaySettlement.ONLINE.value,
            provider_code="viva_wallet",
        )

    def test_a_new_order_records_the_chosen_key(self):
        order = OrderFactory(pay_way=self.courier_cash)

        order.refresh_from_db()
        self.assertEqual(order.pay_way_key, PayWayEnum.PAY_ON_DELIVERY.value)

    def test_changing_the_pay_way_moves_the_snapshot(self):
        """An operator re-pointing an order in the admin means the
        label should follow — it is a correction, not history."""
        order = OrderFactory(pay_way=self.courier_cash)

        order.pay_way = self.card
        order.save()
        order.refresh_from_db()

        self.assertEqual(order.pay_way_key, PayWayEnum.CREDIT_CARD.value)

    def test_renaming_the_pay_way_does_not_rewrite_history(self):
        """The case ``pay_way`` migration 0022 actually created."""
        order = OrderFactory(pay_way=self.courier_cash)
        self.assertEqual(order.pay_way_key, PayWayEnum.PAY_ON_DELIVERY.value)

        self.courier_cash.translations.update(
            name=PayWayEnum.BOX_NOW_PAY_ON_THE_GO.value
        )
        cache.clear()
        order.save()
        order.refresh_from_db()

        self.assertEqual(
            order.pay_way_key,
            PayWayEnum.PAY_ON_DELIVERY.value,
            "a rename must not retroactively change what the shopper "
            "is recorded as having chosen",
        )

    def test_the_snapshot_survives_deleting_the_pay_way(self):
        order = OrderFactory(pay_way=self.courier_cash)
        self.courier_cash.delete()

        order.refresh_from_db()

        self.assertIsNone(order.pay_way)
        self.assertEqual(order.pay_way_key, PayWayEnum.PAY_ON_DELIVERY.value)

    def test_a_partial_save_still_writes_the_snapshot(self):
        """``update_fields`` drops any column it does not name, so the
        write has to be added to the set — exactly what bit
        ``updated_at`` for every partial save before it was fixed."""
        order = OrderFactory(pay_way=self.courier_cash)
        Order.objects.filter(pk=order.pk).update(pay_way_key="")
        order.refresh_from_db()
        self.assertEqual(order.pay_way_key, "")

        order.status = order.status
        order.save(update_fields=["status"])
        order.refresh_from_db()

        self.assertEqual(order.pay_way_key, PayWayEnum.PAY_ON_DELIVERY.value)

    def test_an_order_with_no_pay_way_keeps_an_empty_snapshot(self):
        order = OrderFactory(pay_way=None)

        order.refresh_from_db()

        self.assertEqual(order.pay_way_key, "")

    def test_clearing_the_pay_way_leaves_the_snapshot_intact(self):
        """Nulling the FK is precisely when the snapshot earns its
        keep; it must not be nulled along with it."""
        order = OrderFactory(pay_way=self.courier_cash)

        order.pay_way = None
        order.save()
        order.refresh_from_db()

        self.assertEqual(order.pay_way_key, PayWayEnum.PAY_ON_DELIVERY.value)


class SnapshotCostsNoQueriesToReadTests(TestCase):
    """The point of a column over a join.

    ``for_list()`` already ``select_related``s ``pay_way``, but parler
    translations are NOT prefetched, so resolving a label through the
    relation costs a query per distinct pay-way on a cold cache. The
    snapshot is on the order row itself.
    """

    def test_reading_the_key_hits_no_extra_query(self):
        pay_way = _pay_way(PayWayEnum.PAY_ON_DELIVERY.value)
        OrderFactory(pay_way=pay_way)
        cache.clear()

        orders = list(Order.objects.for_list())
        with self.assertNumQueries(0):
            for order in orders:
                self.assertTrue(order.pay_way_key)
