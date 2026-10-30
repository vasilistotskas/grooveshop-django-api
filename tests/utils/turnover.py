"""Give a hand-built order what checkout and shipping write for it.

The turnover report reads the VAT lines checkout snapshots
(``order.vat_snapshot``) and the moment the order left the store
(``Order.shipped_at``). ``OrderFactory`` writes neither, so a test that
needs a figure ships the order here.
"""

from __future__ import annotations

from datetime import datetime

from order.models import Order
from order.vat_snapshot import write_vat_lines


def snapshot_sale(order: Order, shipped_at: datetime | None) -> Order:
    """Write ``order``'s VAT lines and set when it shipped (``None``: it
    has not)."""
    write_vat_lines(order)
    Order.objects.filter(pk=order.pk).update(shipped_at=shipped_at)
    order.refresh_from_db()
    return order
