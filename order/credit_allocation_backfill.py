"""Allocate money that was booked without an allocation.

Every write path decides the order's unallocated sources before it reads or
takes room (``credit_allocation.allocate_order``), so a source nobody has
allocated is only ever one that no write path has touched since. The
periodic task ``order.tasks.allocate_credit_sources`` runs
:func:`backfill_credit_allocations` for the rest: what a release that did
not know about allocations booked while it still served after the migration
that created the table, a source whose room was held by a claim that has
resolved since (``credit_allocation.allocate_source``), and anything a
failure left behind:

* a refund booked without a claim (a provider's webhook) and not credited;
* a provider refund attempt not declined, not credited;
* a chargeback decision that the goods came back, not credited.

It only chooses the orders; each is decided by ``allocate_order`` on the
live models under its row lock, so there is one definition of the headroom,
of the duplicate-payment excess and of what "allocated" means. A source that
owes a credit is allocated exactly once and is then left alone (a source
that owes nothing is marked by a row of amount zero).

Money that moved before its order's receipt row existed is left alone: the
receipt was issued net of it and owes no credit. Stored-value attempts need
the gift-card and points ledgers and are allocated when claimed.
"""

from __future__ import annotations

import logging

from django.db import transaction

from order import credit_allocation
from order.models import Order

logger = logging.getLogger(__name__)


def backfill_credit_allocations() -> int:
    """Decide every source that owes a credit and has no allocation;
    returns how many sources were given one."""
    done = 0
    for order_id in sorted(
        {o.order_id for o in credit_allocation.owing_sources()}
    ):
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_id)
            done += credit_allocation.allocate_order(
                order, request_credits=True
            )
    return done
