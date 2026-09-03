"""Migration 0051 leaves exactly one spelling of "confirmation sent".

The send used to be recorded as a bare boolean and later as a timestamp,
and the reader accepted either, so every future reader had to know both
shapes. The rows are converted and the fallback removed.

Exercises the migration's actual SQL. It is one set-based statement on
purpose: ``metadata`` is a single jsonb blob, so a read-modify-write loop
would lose any concurrent update to another key on the same row — and
every other writer takes a row lock the migration would not be holding.
"""

from __future__ import annotations

import importlib

import pytest
from django.db import connection

from order.factories.order import OrderFactory
from order.models.order import Order

pytestmark = pytest.mark.django_db

_migration = importlib.import_module(
    "order.migrations.0051_normalise_confirmation_email_flag"
)

LEGACY = "confirmation_email_sent"
TIMESTAMP = "confirmation_email_sent_at"


def _run():
    with connection.cursor() as cursor:
        cursor.execute(_migration.NORMALISE)


def _order(metadata):
    order = OrderFactory(num_order_items=0)
    Order.objects.filter(pk=order.pk).update(metadata=metadata)
    order.refresh_from_db()
    return order


def test_a_legacy_boolean_becomes_a_timestamp():
    order = _order({LEGACY: True})

    _run()

    order.refresh_from_db()
    assert LEGACY not in order.metadata
    assert order.metadata[TIMESTAMP]


def test_the_backfilled_timestamp_is_the_rows_own_updated_at():
    """There is no real send time to recover, so the row's own
    last-modified time is the closest honest answer.

    Compared as an instant: the statement writes UTC while Django hands
    the column back in the project timezone, so the two strings differ
    while denoting the same moment.
    """
    from datetime import datetime
    from datetime import timezone as dt_timezone

    order = _order({LEGACY: True})

    _run()

    order.refresh_from_db()
    stamped = datetime.fromisoformat(order.metadata[TIMESTAMP])
    assert stamped.tzinfo is not None, "the timestamp must be offset-aware"
    assert stamped.astimezone(dt_timezone.utc) == order.updated_at.astimezone(
        dt_timezone.utc
    )


def test_a_row_that_already_has_the_timestamp_keeps_it():
    stamped = "2026-01-01T00:00:00.000000+00:00"
    order = _order({LEGACY: True, TIMESTAMP: stamped})

    _run()

    order.refresh_from_db()
    assert order.metadata[TIMESTAMP] == stamped
    assert LEGACY not in order.metadata


def test_a_false_boolean_does_not_claim_a_send():
    order = _order({LEGACY: False})

    _run()

    order.refresh_from_db()
    assert LEGACY not in order.metadata
    assert TIMESTAMP not in order.metadata


def test_other_metadata_on_the_same_row_survives():
    order = _order({LEGACY: True, "cart_snapshot": {"cart_uuid": "abc"}})

    _run()

    order.refresh_from_db()
    assert order.metadata["cart_snapshot"] == {"cart_uuid": "abc"}
    assert order.metadata[TIMESTAMP]


def test_untouched_orders_are_left_alone():
    order = _order({"something_else": 1})

    _run()

    order.refresh_from_db()
    assert order.metadata == {"something_else": 1}


def test_running_twice_changes_nothing():
    order = _order({LEGACY: True})

    _run()
    order.refresh_from_db()
    first = dict(order.metadata)
    _run()
    order.refresh_from_db()

    assert order.metadata == first


def test_the_converted_row_reads_as_sent():
    """The point of the whole exercise: the reader, which no longer knows
    the boolean, must still see these orders as already sent."""
    from order.tasks import _confirmation_already_sent

    order = _order({LEGACY: True})

    _run()

    order.refresh_from_db()
    assert _confirmation_already_sent(order.metadata) is True
