"""The one lock order for an order and its carrier shipment.

**The order row first, then the shipment row.** A carrier path that needs
both (it reads the order under the lock, writes it, or inserts a row that
references it) takes them through :func:`lock_with_order` and nowhere
else.

Two transactions that lock the same two rows in opposite orders wait on
each other until PostgreSQL aborts one. The order is the row everything
else already queues on (checkout, payment, cancel, refund), so it goes
first. The other way round was a joined
``select_for_update().select_related("order")``: one statement, which
locks the shipment's row before its order's.

A path that only ever touches the shipment locks the shipment alone, and
must not then write the order in the same transaction. An order cancel
holds no shipment lock at all: it commits, and only then asks the courier
(``OrderLifecycleService.cancel_attached_shipment``).
"""

from __future__ import annotations

from django.db.models import Model, QuerySet


def lock_with_order[ShipmentT: Model](
    shipments: QuerySet[ShipmentT],
) -> ShipmentT:
    """Lock the one shipment of *shipments* and its order, the order first.

    Call inside ``transaction.atomic()``. The shipment model has an
    ``order`` foreign key; ``shipment.order`` of the returned row is the
    locked order. Raises the shipment model's ``DoesNotExist`` when
    *shipments* is empty.
    """
    from order.models.order import Order

    order_id = shipments.values_list("order_id", flat=True).get()
    order = Order.objects.select_for_update().get(pk=order_id)
    shipment = shipments.select_for_update().get()
    shipment.order = order
    return shipment
