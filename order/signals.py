from django.dispatch import receiver
from django.dispatch import Signal

from order.models.order import Order

# Signal that order has been created
order_created = Signal()


@receiver(order_created)
def handle_order_created(sender, **kwargs):
    order: Order = kwargs["order"]

    items = order.order_item_order.all()
    for item in items:
        product = item.product
        product.decrement_stock(item.quantity)

    order.paid_amount = order.calculate_order_total_amount()
    order.save(update_fields=["paid_amount"])
