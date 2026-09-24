from django.db import models
from django.utils.translation import gettext_lazy as _


class OrderCreateErrorType(models.TextChoices):
    """``error.type`` of a refused order create (``POST /order``).

    The stable code a client branches on. The ``detail`` beside it is
    translated into the request's language, so matching its text works
    in one language at most.
    """

    INSUFFICIENT_STOCK = "insufficient_stock", _("Insufficient stock")
    CART_INVALID = "cart_invalid", _("Cart not ready for checkout")
    RESERVATION_UNAVAILABLE = (
        "reservation_unavailable",
        _("Stock reservation no longer valid"),
    )
    INVALID_ORDER_DATA = "invalid_order_data", _("Invalid order data")
    INVALID_COUPON = "invalid_coupon", _("Invalid coupon")
    INVALID_GIFT_CARD = "invalid_gift_card", _("Invalid gift card")
    PAYMENT_NOT_FOUND = "payment_not_found", _("Payment not found")
    PAYMENT_VERIFICATION = (
        "payment_verification",
        _("Payment verification failed"),
    )
    PAYMENT_AMOUNT_MISMATCH = (
        "payment_amount_mismatch",
        _("Payment amount mismatch"),
    )
    PAYMENT_CURRENCY_MISMATCH = (
        "payment_currency_mismatch",
        _("Payment currency mismatch"),
    )
