"""``store_value_in_metadata`` must not clobber another writer's keys.

``metadata`` is a single jsonb column, so writing it back from an
instance loaded earlier replaces the WHOLE document — every key another
transaction added in between is silently gone.

That is reachable today through ``POST /api/v1/loyalty/redeem``
(``loyalty/views/loyalty.py``), which loads the order with a bare
``Order.objects.get()`` — no ``select_for_update``, no surrounding
``transaction.atomic`` — runs its validation, and only then writes. Any
metadata write committed against that order in the meantime is lost.

The key most likely to be lost is the worst one to lose:
``viva_order_codes``. It is how both the Viva webhook and the browser
return endpoint find the order (``viva_order_code_q``), so dropping it
strands a PAID order as unmatched, and the auto-cancel sweep cancels it
a day later.
"""

from __future__ import annotations

import pytest

from order.factories.order import OrderFactory
from order.models.order import Order

pytestmark = pytest.mark.django_db


def _order_with(metadata: dict):
    order = OrderFactory(num_order_items=0)
    Order.objects.filter(pk=order.pk).update(metadata=metadata)
    order.refresh_from_db()
    return order


def test_a_key_written_after_load_is_not_clobbered():
    """The /loyalty/redeem shape: load, wait, write."""
    order = _order_with({"cart_snapshot": {"cart_uuid": "abc"}})

    # The redeem endpoint's `.get()` — its snapshot has no Viva code.
    redeeming = Order.objects.get(pk=order.pk)

    # A hosted-checkout session is minted while that request is in flight.
    concurrent = Order.objects.get(pk=order.pk)
    concurrent.metadata["viva_order_codes"] = ["OC-PAID-1"]
    concurrent.save(update_fields=["metadata"])

    redeeming.store_value_in_metadata({"loyalty_points_redeemed": 100})

    fresh = Order.objects.get(pk=order.pk)
    assert fresh.metadata["loyalty_points_redeemed"] == 100
    assert fresh.metadata["viva_order_codes"] == ["OC-PAID-1"], (
        "the Viva order code was overwritten by a stale snapshot; the "
        "webhook can no longer find this order"
    )
    assert fresh.metadata["cart_snapshot"] == {"cart_uuid": "abc"}


def test_it_still_merges_its_own_keys():
    order = _order_with({"existing": 1})

    order.store_value_in_metadata({"a": 1, "b": {"nested": True}})

    fresh = Order.objects.get(pk=order.pk)
    assert fresh.metadata == {"existing": 1, "a": 1, "b": {"nested": True}}


def test_it_overwrites_a_key_it_owns():
    order = _order_with({"loyalty_points_redeemed": 1})

    order.store_value_in_metadata({"loyalty_points_redeemed": 2})

    assert Order.objects.get(pk=order.pk).metadata == {
        "loyalty_points_redeemed": 2
    }


def test_the_in_memory_instance_matches_the_row():
    """Callers keep using the instance after the write.

    ``order/services.py`` sets ``order.metadata["loyalty_redemption"]``
    immediately after ``redeem_points`` returns and saves later, so the
    instance must carry what the database now holds.
    """
    order = _order_with({"existing": 1})

    order.store_value_in_metadata({"added": 2})

    assert order.metadata == Order.objects.get(pk=order.pk).metadata


def test_empty_items_is_a_no_op():
    order = _order_with({"existing": 1})

    order.store_value_in_metadata({})

    assert Order.objects.get(pk=order.pk).metadata == {"existing": 1}
