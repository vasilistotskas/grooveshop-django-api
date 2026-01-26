from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel


class StockLog(TimeStampMixinModel):
    """
    Audit log for all stock operations.

    Records every stock change with before/after values for complete auditability.
    This provides a comprehensive audit trail for inventory management, allowing
    tracking of all stock movements including reservations, releases, decrements,
    and increments.

    Operation Types:
    - RESERVE: Stock reserved during checkout
    - RELEASE: Reservation released (expired or cancelled)
    - DECREMENT: Stock permanently decreased (order confirmed)
    - INCREMENT: Stock increased (order cancelled or returned)

    Each log entry captures:
    - The exact stock levels before and after the operation
    - The reason for the change
    - The user who performed the operation (if applicable)
    - The associated order (if applicable)
    - Timestamp of the operation (via TimeStampMixinModel)
    """

    OPERATION_RESERVE = "RESERVE"
    OPERATION_RELEASE = "RELEASE"
    OPERATION_DECREMENT = "DECREMENT"
    OPERATION_INCREMENT = "INCREMENT"

    OPERATION_TYPE_CHOICES = [
        (OPERATION_RESERVE, _("Reserve")),
        (OPERATION_RELEASE, _("Release")),
        (OPERATION_DECREMENT, _("Decrement")),
        (OPERATION_INCREMENT, _("Increment")),
    ]

    id = models.BigAutoField(primary_key=True)
    product = models.ForeignKey(
        "product.Product",
        related_name="stock_logs",
        on_delete=models.CASCADE,
        help_text=_("Product whose stock was modified"),
    )
    order = models.ForeignKey(
        "order.Order",
        related_name="stock_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text=_(
            "Order associated with this stock operation (if applicable)"
        ),
    )
    operation_type = models.CharField(
        _("Operation Type"),
        max_length=50,
        choices=OPERATION_TYPE_CHOICES,
        help_text=_("Type of stock operation performed"),
    )
    quantity_delta = models.IntegerField(
        _("Quantity Delta"),
        help_text=_(
            "Change in quantity (positive for increment, negative for decrement)"
        ),
    )
    stock_before = models.IntegerField(
        _("Stock Before"),
        help_text=_("Stock level before the operation"),
    )
    stock_after = models.IntegerField(
        _("Stock After"),
        help_text=_("Stock level after the operation"),
    )
    reason = models.CharField(
        _("Reason"),
        max_length=255,
        help_text=_("Human-readable reason for the stock change"),
    )
    performed_by = models.ForeignKey(
        "user.UserAccount",
        related_name="stock_operations",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text=_(
            "User who performed the operation (null for system operations)"
        ),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Stock Log")
        verbose_name_plural = _("Stock Logs")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(
                fields=["product", "created_at"],
                name="stock_log_product_created_ix",
            ),
            BTreeIndex(
                fields=["order"],
                name="stock_log_order_ix",
            ),
            BTreeIndex(
                fields=["operation_type"],
                name="stock_log_operation_type_ix",
            ),
        ]

    def __str__(self) -> str:
        product_name = self.product.safe_translation_getter(
            "name", any_language=True
        )
        return (
            f"StockLog {self.id} - {product_name}: "
            f"{self.operation_type} ({self.stock_before} → {self.stock_after})"
        )

    @property
    def is_increase(self) -> bool:
        """Check if this operation increased stock."""
        return self.quantity_delta > 0

    @property
    def is_decrease(self) -> bool:
        """Check if this operation decreased stock."""
        return self.quantity_delta < 0

    def clean(self) -> None:
        """
        Validate stock calculations based on operation type.

        For DECREMENT and INCREMENT operations (which change physical stock):
            stock_after must equal stock_before + quantity_delta

        For RESERVE and RELEASE operations (which don't change physical stock):
            stock_after must equal stock_before (no physical change)
            quantity_delta represents the reservation amount (not a physical change)
        """
        from django.core.exceptions import ValidationError

        # For operations that change physical stock (DECREMENT, INCREMENT)
        # Validate: stock_after == stock_before + quantity_delta
        if self.operation_type in [
            self.OPERATION_DECREMENT,
            self.OPERATION_INCREMENT,
        ]:
            if self.stock_after != self.stock_before + self.quantity_delta:
                raise ValidationError(
                    _(
                        "Stock calculation error: For {operation}, stock_after must equal "
                        "stock_before + quantity_delta. Got: {after} != {before} + {delta}"
                    ).format(
                        operation=self.get_operation_type_display(),
                        after=self.stock_after,
                        before=self.stock_before,
                        delta=self.quantity_delta,
                    )
                )

        # For operations that don't change physical stock (RESERVE, RELEASE)
        # Validate: stock_after == stock_before (no physical change)
        elif self.operation_type in [
            self.OPERATION_RESERVE,
            self.OPERATION_RELEASE,
        ]:
            if self.stock_after != self.stock_before:
                raise ValidationError(
                    _(
                        "Stock calculation error: For {operation}, stock_after must equal "
                        "stock_before (no physical stock change). Got: {after} != {before}"
                    ).format(
                        operation=self.get_operation_type_display(),
                        after=self.stock_after,
                        before=self.stock_before,
                    )
                )
