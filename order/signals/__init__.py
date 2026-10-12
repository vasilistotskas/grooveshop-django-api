from django.dispatch import Signal

order_created = Signal()
order_status_changed = Signal()
order_paid = Signal()
order_canceled = Signal()
order_shipped = Signal()
order_delivered = Signal()
order_completed = Signal()
# Dispatched once per REFUND row recorded on the payment ledger
# (``PaymentLedger.record_refund``), after ``order.payment_status`` was
# derived from it. Keyword arguments:
#
# * ``amount`` (``Money``, always present): what THIS refund paid back.
#   Receivers that use it declare it as a required argument, so a sender
#   that leaves it out fails loudly instead of being read as "everything".
# * ``reason`` (``str``, optional).
#
# ``order.payment_status`` says how much of the order is back:
# PARTIALLY_REFUNDED while part of the payment remains refundable,
# REFUNDED once nothing does (``PaymentLedger.refund_status``). Whatever
# hangs off the order's total — the gift-card redemption, the loyalty
# points — is released only on REFUNDED, once, with the last refund; a
# slice per partial refund cannot be made whole again by the next one.
order_refunded = Signal()
order_returned = Signal()
# Dispatched the first time an order gets a tracking number / carrier.
# Kept distinct from ``order_shipped`` (which fires on status transition
# to SHIPPED) because admins sometimes add tracking before flipping
# status, or flip status before tracking arrives — the two events are
# naturally separate from the shopper's point of view ("we marked it
# shipped" vs "here's how to track it").
order_shipment_dispatched = Signal()
