from typing import TYPE_CHECKING

from django.dispatch import Signal, receiver

if TYPE_CHECKING:
    from order.models.order import Order

# Signal that order has been created
order_created = Signal()


@receiver(order_created)
def handle_order_created(sender, **kwargs):
    order: Order = kwargs["order"]

    items = order.items.all()
    for item in items:
        product = item.product
        product.decrement_stock(item.quantity)

    order.paid_amount = order.calculate_order_total_amount()
    order.save(update_fields=["paid_amount"])
