from django.dispatch import Signal

order_created = Signal()
order_status_changed = Signal()
order_paid = Signal()
order_canceled = Signal()
order_shipped = Signal()
order_delivered = Signal()
order_completed = Signal()
order_refunded = Signal()
order_returned = Signal()
# Dispatched the first time an order gets a tracking number / carrier.
# Kept distinct from ``order_shipped`` (which fires on status transition
# to SHIPPED) because admins sometimes add tracking before flipping
# status, or flip status before tracking arrives — the two events are
# naturally separate from the shopper's point of view ("we marked it
# shipped" vs "here's how to track it").
order_shipment_dispatched = Signal()
