from decimal import Decimal

from django.contrib.postgres.indexes import BTreeIndex
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.expressions import DatabaseDefault
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from djmoney.money import Money

from core.models import SortableModel, TimeStampMixinModel, UUIDModel
from order.enum.status import RETURN_HANDLING_STATUSES
from order.managers.item import OrderItemManager
from vat.models import validate_mydata_vat_rate

# The restock reason is written to ``StockLog.reason`` (255) after a prefix of
# about 45 characters, so it is capped well under that wherever it is typed.
RESTOCK_REASON_MAX_LENGTH = 150


class OrderItem(TimeStampMixinModel, SortableModel, UUIDModel):
    id = models.BigAutoField(primary_key=True)
    order = models.ForeignKey(
        "order.Order",
        related_name="items",
        on_delete=models.CASCADE,
        verbose_name=_("Order"),
    )
    product = models.ForeignKey(
        "product.Product",
        related_name="order_items",
        on_delete=models.PROTECT,
        verbose_name=_("Product"),
    )
    price = MoneyField(_("Price"), max_digits=11, decimal_places=2)
    # The VAT percentage ``price`` was sold at (``price`` is VAT-inclusive),
    # fixed when the line is created. The invoice and the myDATA payload
    # split ``price`` at this rate and never read ``product.vat`` again, so
    # editing or deleting a ``Vat`` row cannot rewrite a document already
    # issued for the order. ``db_default`` exists for the rolling deploy
    # (the previous release still INSERTs without the column) and for the
    # backfill of existing rows; every creation path states the rate, and
    # ``save`` snapshots the product's current one when a caller does not.
    vat_rate = models.DecimalField(
        _("VAT Rate"),
        max_digits=11,
        decimal_places=1,
        db_default=Decimal(0),
        validators=[validate_mydata_vat_rate],
        help_text=_(
            "VAT percentage this line was sold at, fixed when the line was "
            "created. Invoices and myDATA use it, never the product's "
            "current VAT."
        ),
    )
    # The part of the order's monetary discount (promotions + loyalty)
    # this line carries, for the whole line, fixed when the order is
    # created. A promotion lands on the lines it covers, so a coupon for
    # one category never reduces a line of another (which may carry a
    # different VAT rate); the invoice and myDATA read this and never
    # re-derive it (``order.discounts``). ``db_default`` is for the
    # rolling deploy: the previous release still INSERTs without the
    # column, and ``heal_discount_allocation`` fills those orders in.
    discount_allocation = MoneyField(
        _("Discount Allocation"),
        max_digits=11,
        decimal_places=2,
        default=0,
        db_default=Decimal(0),
        help_text=_(
            "Part of the order's discount this line carries, fixed when "
            "the order was created. Invoices and myDATA use it to split "
            "the VAT of what was actually charged."
        ),
    )
    quantity = models.IntegerField(_("Quantity"), default=1)
    original_quantity = models.IntegerField(
        _("Original Quantity"), null=True, blank=True
    )
    is_refunded = models.BooleanField(_("Is Refunded"), default=False)
    refunded_quantity = models.IntegerField(_("Refunded Quantity"), default=0)
    notes = models.TextField(_("Notes"), blank=True, default="")
    # Carried over from ``CartItem.recommendation_impression_id`` at
    # checkout: the suggestion-strip impression this line was added
    # from. What ``recommendation.events.record_attach_events`` matches
    # first, before the cart and customer windows.
    recommendation_impression_id = models.UUIDField(
        _("Recommendation impression"), null=True, blank=True
    )

    objects: OrderItemManager = OrderItemManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Order Item")
        verbose_name_plural = _("Order Items")
        ordering = ["sort_order"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            *SortableModel.Meta.indexes,
            BTreeIndex(fields=["product"], name="order_item_product_ix"),
            BTreeIndex(
                fields=["is_refunded"], name="order_item_is_refunded_ix"
            ),
        ]
        constraints = [
            # DB-level invariants — application code should already uphold
            # these, but the constraints stop a bad path (or manual SQL)
            # from writing an oversold/over-refunded line item (G0247).
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="order_item_quantity_gte_1",
            ),
            models.CheckConstraint(
                condition=models.Q(refunded_quantity__gte=0)
                & models.Q(refunded_quantity__lte=models.F("quantity")),
                name="order_item_refunded_within_quantity",
            ),
        ]

    def __str__(self):
        product_name = self.product.safe_translation_getter(
            "name", any_language=True
        )
        return gettext("Order %(order)s - %(product)s × %(quantity)s") % {
            "order": self.order_id,
            "product": product_name,
            "quantity": self.quantity,
        }

    def clean(self):
        if self.quantity <= 0:
            raise ValidationError(_("Quantity must be greater than 0."))

        if (
            hasattr(self.product, "stock")
            and self.quantity > self.product.stock
            and not self.pk
        ):
            raise ValidationError(
                _("The quantity exceeds the available stock.")
            )

        if self.refunded_quantity > self.quantity:
            raise ValidationError(
                _("Refunded quantity cannot exceed the ordered quantity.")
            )

    @property
    def total_price(self) -> Money:
        return Money(
            amount=self.price.amount * Decimal(self.quantity),
            currency=self.price.currency,
        )

    @property
    def refunded_amount(self) -> Money:
        if self.refunded_quantity == 0:
            return Money(amount=0, currency=self.price.currency)

        return Money(
            amount=self.price.amount * Decimal(self.refunded_quantity),
            currency=self.price.currency,
        )

    @property
    def net_quantity(self) -> int:
        return self.quantity - self.refunded_quantity

    @property
    def net_price(self) -> Money:
        return Money(
            amount=self.price.amount * Decimal(self.net_quantity),
            currency=self.price.currency,
        )

    def save(self, *args, **kwargs):
        if not self.pk and self.original_quantity is None:
            self.original_quantity = self.quantity
        if self._state.adding and isinstance(self.vat_rate, DatabaseDefault):
            # Nobody stated a rate (admin, staff API, a script): the line's
            # snapshot is the rate its product carries right now.
            self.vat_rate = self.product.vat_percent
        super().save(*args, **kwargs)

    def refund(self, quantity=None, *, performed_by=None, reason=""):
        """Put ``quantity`` returned units back in stock and record them as
        refunded on this line. Moves NO money: what the customer is paid
        back is the order's refund (``OrderService.refund_order``), because
        a line's snapshot price is not what was paid for it. Returns the
        value of the units, for the caller's display.

        ``performed_by`` and ``reason`` go onto the stock log row: who put
        the units back, and why.
        """
        from django.db import transaction

        from order.stock import StockManager

        with transaction.atomic():
            # The order first, then the line: the admin action restocks
            # several lines under the order lock, so every path takes the
            # two in that order. The status is judged under the lock, so a
            # cancel or a status change cannot slip between check and write.
            order = (
                type(self.order)
                .objects.select_for_update()
                .get(pk=self.order_id)
            )
            if order.status not in RETURN_HANDLING_STATUSES:
                raise ValidationError(
                    _(
                        "Units can only be restocked on a delivered, "
                        "completed or returned order."
                    )
                )

            # Lock this item so concurrent refunds serialize on
            # refunded_quantity — otherwise both read a stale value, both pass
            # the over-refund guard, and stock is restocked twice.
            item = type(self).objects.select_for_update().get(pk=self.pk)

            # No quantity means "refund what is still refundable", which is
            # the remainder — not the originally ordered quantity. Using
            # the latter made the documented empty-body call fail on any
            # partially refunded item (5 ordered, 2 already refunded: the
            # default asked for 5 more and tripped the over-refund guard),
            # so the last units could only be refunded by naming the exact
            # remainder.
            remaining = item.quantity - item.refunded_quantity
            refund_qty = quantity if quantity is not None else remaining

            if refund_qty <= 0:
                raise ValidationError(
                    _(
                        "Invalid refund quantity. Please enter a quantity greater than 0."
                    )
                )

            if item.refunded_quantity + refund_qty > item.quantity:
                # %-format, not an f-string: an f-string is resolved
                # BEFORE `_()` sees it, so the msgid carries the actual
                # numbers and can never match a catalogue entry — this
                # message reached Greek users untranslated.
                raise ValidationError(
                    _(
                        "Cannot refund more than the ordered quantity. "
                        "Ordered quantity: %(ordered)s, "
                        "Refunded quantity: %(refunded)s, "
                        "Refund quantity: %(requested)s"
                    )
                    % {
                        "ordered": item.quantity,
                        "refunded": item.refunded_quantity,
                        "requested": refund_qty,
                    }
                )

            if hasattr(item.product, "stock"):
                # Restock through StockManager so the product row is locked
                # and a StockLog audit row is written (the previous raw F()
                # update did neither).
                StockManager.increment_stock(
                    product_id=item.product_id,
                    quantity=refund_qty,
                    order_id=item.order_id,
                    reason=(
                        f"Refund of {refund_qty} unit(s) for order item "
                        f"{item.pk}: {reason}"
                        if reason
                        else f"Refund of {refund_qty} unit(s) for order item "
                        f"{item.pk}"
                    ),
                    performed_by=performed_by,
                )

            item.refunded_quantity += refund_qty
            if item.refunded_quantity == item.quantity:
                item.is_refunded = True

            item.save(update_fields=["refunded_quantity", "is_refunded"])

            # Keep the in-memory instance consistent with the locked row.
            self.refunded_quantity = item.refunded_quantity
            self.is_refunded = item.is_refunded

        return Money(
            amount=self.price.amount * Decimal(refund_qty),
            currency=self.price.currency,
        )

    def get_ordering_queryset(self):
        return self.order.items.all()
