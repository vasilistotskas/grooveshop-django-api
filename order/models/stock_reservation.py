from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel


class StockReservation(TimeStampMixinModel):
    """
    Temporary stock hold during checkout process.

    Represents a reservation of product inventory for a specific duration
    (default 15 minutes) to prevent stock from being sold to other customers
    while a user completes their checkout.

    Lifecycle:
    1. Created when customer begins checkout (consumed=False)
    2. Expires after TTL if payment not completed
    3. Converted to stock decrement when payment succeeds (consumed=True)
    4. Released if checkout abandoned or payment fails
    """

    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(
        "product.Product",
        related_name="stock_reservations",
        on_delete=models.CASCADE,
        help_text=_("Product being reserved"),
    )
    quantity = models.PositiveIntegerField(
        _("Quantity"),
        help_text=_("Number of units reserved"),
    )
    reserved_by = models.ForeignKey(
        "user.UserAccount",
        related_name="stock_reservations",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text=_("User who made the reservation (null for guest users)"),
    )
    session_id = models.CharField(
        _("Session ID"),
        max_length=255,
        help_text=_("Cart UUID or session identifier for tracking"),
    )
    expires_at = models.DateTimeField(
        _("Expires At"),
        help_text=_(
            "Timestamp when reservation expires (typically 15 minutes from creation)"
        ),
    )
    consumed = models.BooleanField(
        _("Consumed"),
        default=False,
        help_text=_(
            "True if reservation was converted to actual stock decrement"
        ),
    )
    order = models.ForeignKey(
        "order.Order",
        related_name="stock_reservations",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text=_("Order created from this reservation (set when consumed)"),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Stock Reservation")
        verbose_name_plural = _("Stock Reservations")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(
                fields=["product", "consumed"],
                name="stock_res_product_consumed_ix",
            ),
            BTreeIndex(
                fields=["expires_at", "consumed"],
                name="stock_res_expires_consumed_ix",
            ),
            BTreeIndex(
                fields=["session_id"],
                name="stock_res_session_id_ix",
            ),
        ]

    def __str__(self) -> str:
        product_name = self.product.safe_translation_getter(
            "name", any_language=True
        )
        status = "consumed" if self.consumed else "active"
        return f"Reservation {self.id} - {product_name} x {self.quantity} ({status})"

    @property
    def is_expired(self) -> bool:
        """Check if reservation has expired."""
        from django.utils import timezone

        return timezone.now() > self.expires_at

    @property
    def is_active(self) -> bool:
        """Check if reservation is still active (not consumed and not expired)."""
        return not self.consumed and not self.is_expired
