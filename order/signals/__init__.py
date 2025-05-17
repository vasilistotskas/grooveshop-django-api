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
