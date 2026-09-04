"""Migration 0052 leaves one list of Viva order codes, not a list plus a
duplicate of its newest entry.

Each hosted-checkout session mints a fresh code. The writer appended it
to ``viva_order_codes`` and also mirrored the newest into a singular
``viva_order_code``, so every lookup had to match either spelling. The
list is the thing that matters — a shopper can pay on an earlier session
— so the singular is folded in and dropped.
"""

from __future__ import annotations

import importlib

import pytest
from django.db import connection

from order.factories.order import OrderFactory
from order.models.order import Order

pytestmark = pytest.mark.django_db

_migration = importlib.import_module(
    "order.migrations.0052_fold_viva_order_code_into_history"
)

SINGULAR = "viva_order_code"
LIST = "viva_order_codes"


def _run():
    with connection.cursor() as cursor:
        cursor.execute(_migration.FOLD)


def _order(metadata):
    order = OrderFactory(num_order_items=0)
    Order.objects.filter(pk=order.pk).update(metadata=metadata)
    order.refresh_from_db()
    return order


def test_a_code_already_in_the_list_is_just_dropped():
    order = _order({SINGULAR: "OC2", LIST: ["OC1", "OC2"]})

    _run()

    order.refresh_from_db()
    assert SINGULAR not in order.metadata
    assert order.metadata[LIST] == ["OC1", "OC2"]


def test_a_code_missing_from_the_list_is_folded_in():
    """Rows written before the list existed carry only the singular."""
    order = _order({SINGULAR: "OC1"})

    _run()

    order.refresh_from_db()
    assert SINGULAR not in order.metadata
    assert order.metadata[LIST] == ["OC1"]


def test_a_singular_not_in_an_existing_list_is_appended():
    order = _order({SINGULAR: "OC3", LIST: ["OC1", "OC2"]})

    _run()

    order.refresh_from_db()
    assert order.metadata[LIST] == ["OC1", "OC2", "OC3"]


def test_other_metadata_on_the_same_row_survives():
    order = _order({SINGULAR: "OC1", "cart_snapshot": {"cart_uuid": "abc"}})

    _run()

    order.refresh_from_db()
    assert order.metadata["cart_snapshot"] == {"cart_uuid": "abc"}
    assert order.metadata[LIST] == ["OC1"]


def test_orders_without_the_singular_are_untouched():
    order = _order({LIST: ["OC1"]})

    _run()

    order.refresh_from_db()
    assert order.metadata == {LIST: ["OC1"]}


def test_running_twice_changes_nothing():
    order = _order({SINGULAR: "OC1", LIST: ["OC1"]})

    _run()
    order.refresh_from_db()
    first = dict(order.metadata)
    _run()
    order.refresh_from_db()

    assert order.metadata == first


def test_the_folded_code_still_resolves_the_order():
    """The point of the exercise: the webhook and the return endpoint
    look the order up through one matcher, which now consults only the
    list."""
    from order.views.viva_webhook import viva_order_code_q

    order = _order({SINGULAR: "OC-ONLY-SINGULAR"})

    _run()

    found = Order.objects.filter(viva_order_code_q("OC-ONLY-SINGULAR")).first()
    assert found is not None, (
        "an order whose only code was the singular became unreachable"
    )
    assert found.pk == order.pk
