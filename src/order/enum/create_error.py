from django.db import models
from django.utils.translation import gettext_lazy as _

# The ``code`` of an order create refused because the request's
# ``expectedTotal`` is not the amount due computed now (409). Not a member
# of ``OrderCreateErrorType`` yet: that enum is in the API contract, and the
# storefront in production must list a value before Django may send it
# (``api-contract/README.md``, "ships consumer first"). Until its release
# does, the refusal carries ``error.type`` ``invalid_order_data``, which
# every client already handles, and clients branch on ``code``. Then this
# becomes ``OrderCreateErrorType.TOTAL_CHANGED`` and both carry it (#635).
TOTAL_CHANGED_CODE = "total_changed"

# The ``code`` of an order create refused because the request's
# ``expectedCartDigest`` is not the cart digest computed now (409): the
# order would be made of something else than what the buyer was shown.
# Sent only to a request carrying ``expectedCartDigest``, under
# ``error.type`` ``invalid_order_data``, until the storefront in
# production lists it (as ``TOTAL_CHANGED_CODE``, #755).
CART_CHANGED_CODE = "cart_changed"


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
    LEGAL_DOCUMENT_UNAVAILABLE = (
        "legal_document_unavailable",
        _("Legal document unavailable"),
    )
