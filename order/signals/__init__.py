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
# PARTIALLY_REFUNDED while part of the payment, or of what the order paid
# with gift cards and points, remains to return, REFUNDED once nothing does
# (``PaymentLedger.refund_status``). The points the order earned are
# reversed once, on REFUNDED; the gift cards and spent points go back only
# as the value of a refund beyond the provider's money
# (``order_stored_value_returned``), never because this signal fired.
order_refunded = Signal()
# Dispatched once per refund share that went back onto gift cards and
# loyalty points (``OrderService._return_stored_value``): value returned
# without a provider moving money, so there is no ledger row and no
# ``order_refunded``. Keyword arguments: ``amount`` (``Money``, the value
# of the share), ``gift_card`` (``Money`` credited to cards), ``points``
# (int restored), ``reason`` and ``previous_payment_status``. The order's
# ``payment_status`` is already derived, so REFUNDED here means the whole
# order is back: the receivers that follow a completed refund (the retail
# settlement document, the reversal of the points it earned) act then.
order_stored_value_returned = Signal()
# Dispatched once per CHARGEBACK row recorded on the payment ledger
# (``PaymentLedger.record_chargeback``): a lost dispute took ``amount``
# (``Money``) back. Deliberately not ``order_refunded``: nothing was paid
# back to the customer, so no refund email, Meta event or gift-card
# release follows. ``chargeback`` is the ledger row.
order_charged_back = Signal()
order_returned = Signal()
# Dispatched the first time an order gets a tracking number / carrier.
# Kept distinct from ``order_shipped`` (which fires on status transition
# to SHIPPED) because admins sometimes add tracking before flipping
# status, or flip status before tracking arrives — the two events are
# naturally separate from the shopper's point of view ("we marked it
# shipped" vs "here's how to track it").
order_shipment_dispatched = Signal()
