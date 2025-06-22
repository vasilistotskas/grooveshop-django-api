from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from parler.models import TranslatableModel, TranslatedFields

from core.models import TimeStampMixinModel, UUIDModel
from order.managers.history import OrderHistoryManager, OrderItemHistoryManager


class OrderHistory(TranslatableModel, TimeStampMixinModel, UUIDModel):
    class OrderHistoryChangeType(models.TextChoices):
        STATUS = "STATUS", _("Status Change")
        PAYMENT = "PAYMENT", _("Payment Update")
        SHIPPING = "SHIPPING", _("Shipping Update")
        CUSTOMER = "CUSTOMER", _("Customer Info Update")
        ITEMS = "ITEMS", _("Items Update")
        ADDRESS = "ADDRESS", _("Address Update")
        NOTE = "NOTE", _("Note Added")
        REFUND = "REFUND", _("Refund Processed")
        OTHER = "OTHER", _("Other Change")

    translations = TranslatedFields(
        description=models.TextField(
            _("Description"),
            blank=True,
            default="",
            help_text=_("Description of what changed."),
        ),
    )

    order = models.ForeignKey(
        "order.Order",
        related_name="history",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="order_changes",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text=_("User who made the change, if applicable."),
    )
    change_type = models.CharField(
        _("Change Type"),
        max_length=20,
        choices=OrderHistoryChangeType,
    )
    previous_value = models.JSONField(
        _("Previous Value"),
        null=True,
        blank=True,
        help_text=_("Previous value of the changed field(s)."),
    )
    new_value = models.JSONField(
        _("New Value"),
        null=True,
        blank=True,
        help_text=_("New value of the changed field(s)."),
    )
    ip_address = models.GenericIPAddressField(
        _("IP Address"),
        null=True,
        blank=True,
        help_text=_("IP address from which the change was made."),
    )
    user_agent = models.TextField(
        _("User Agent"),
        blank=True,
        help_text=_("User agent of the browser/client that made the change."),
    )

    objects: OrderHistoryManager = OrderHistoryManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Order History")
        verbose_name_plural = _("Order History")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(
                fields=["order", "change_type"],
                name="ord_hist_ord_chtype_ix",
            ),
            BTreeIndex(
                fields=["order", "-created_at"],
                name="ord_hist_ord_crtd_ix",
            ),
            BTreeIndex(fields=["change_type"], name="ord_hist_chtype_ix"),
        ]

    def __str__(self):
        return f"Order {self.order.id} - {self.get_change_type_display()} - {self.created_at}"

    @classmethod
    def log_status_change(
        cls, order, previous_status, new_status, user=None, request=None
    ):
        ip_address = None
        user_agent = ""

        if request:
            ip_address = request.META.get("REMOTE_ADDR")
            user_agent = request.META.get("HTTP_USER_AGENT", "")

        return cls.objects.create(
            order=order,
            user=user,
            change_type="STATUS",
            previous_value={"status": previous_status},
            new_value={"status": new_status},
            description=f"Status changed from {previous_status} to {new_status}",
            ip_address=ip_address,
            user_agent=user_agent,
        )

    @classmethod
    def log_payment_update(
        cls, order, previous_value, new_value, user=None, request=None
    ):
        ip_address = None
        user_agent = ""

        if request:
            ip_address = request.META.get("REMOTE_ADDR")
            user_agent = request.META.get("HTTP_USER_AGENT", "")

        if isinstance(previous_value, dict):
            previous_value = {
                k: str(v)
                if not isinstance(
                    v, int | float | bool | type(None) | list | dict
                )
                else v
                for k, v in previous_value.items()
            }

        if isinstance(new_value, dict):
            new_value = {
                k: str(v)
                if not isinstance(
                    v, int | float | bool | type(None) | list | dict
                )
                else v
                for k, v in new_value.items()
            }

        return cls.objects.create(
            order=order,
            user=user,
            change_type="PAYMENT",
            previous_value=previous_value,
            new_value=new_value,
            description="Payment information updated",
            ip_address=ip_address,
            user_agent=user_agent,
        )

    @classmethod
    def log_shipping_update(
        cls, order, previous_value, new_value, user=None, request=None
    ):
        ip_address = None
        user_agent = ""

        if request:
            ip_address = request.META.get("REMOTE_ADDR")
            user_agent = request.META.get("HTTP_USER_AGENT", "")

        return cls.objects.create(
            order=order,
            user=user,
            change_type="SHIPPING",
            previous_value=previous_value,
            new_value=new_value,
            description="Shipping information updated",
            ip_address=ip_address,
            user_agent=user_agent,
        )

    @classmethod
    def log_note(cls, order, note, user=None, request=None):
        ip_address = None
        user_agent = ""

        if request:
            ip_address = request.META.get("REMOTE_ADDR")
            user_agent = request.META.get("HTTP_USER_AGENT", "")

        return cls.objects.create(
            order=order,
            user=user,
            change_type="NOTE",
            new_value={"note": note},
            description="Note added to order",
            ip_address=ip_address,
            user_agent=user_agent,
        )

    @classmethod
    def log_refund(cls, order, refund_data, user=None, request=None):
        ip_address = None
        user_agent = ""

        if request:
            ip_address = request.META.get("REMOTE_ADDR")
            user_agent = request.META.get("HTTP_USER_AGENT", "")

        return cls.objects.create(
            order=order,
            user=user,
            change_type="REFUND",
            new_value=refund_data,
            description=f"Refund processed for {refund_data.get('amount', 'unknown amount')}",
            ip_address=ip_address,
            user_agent=user_agent,
        )


class OrderItemHistory(TranslatableModel, TimeStampMixinModel, UUIDModel):
    class OrderItemHistoryChangeType(models.TextChoices):
        QUANTITY = "QUANTITY", _("Quantity Change")
        PRICE = "PRICE", _("Price Update")
        REFUND = "REFUND", _("Item Refund")
        OTHER = "OTHER", _("Other Change")

    translations = TranslatedFields(
        description=models.TextField(
            _("Description"),
            blank=True,
            default="",
            help_text=_("Description of what changed."),
        ),
    )

    order_item = models.ForeignKey(
        "order.OrderItem",
        related_name="history",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        "user.UserAccount",
        related_name="order_item_changes",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text=_("User who made the change, if applicable."),
    )
    change_type = models.CharField(
        _("Change Type"),
        max_length=20,
        choices=OrderItemHistoryChangeType,
    )
    previous_value = models.JSONField(
        _("Previous Value"),
        null=True,
        blank=True,
        help_text=_("Previous value of the changed field(s)."),
    )
    new_value = models.JSONField(
        _("New Value"),
        null=True,
        blank=True,
        help_text=_("New value of the changed field(s)."),
    )
    objects: OrderItemHistoryManager = OrderItemHistoryManager()

    class Meta(TypedModelMeta):
        verbose_name = _("Order Item History")
        verbose_name_plural = _("Order Item History")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(
                fields=["order_item", "-created_at"],
                name="ord_item_hist_item_created_ix",
            ),
            BTreeIndex(
                fields=["change_type"],
                name="ord_item_hist_change_type_ix",
            ),
        ]

    def __str__(self):
        return f"Order Item {self.order_item.id} - {self.get_change_type_display()} - {self.created_at}"

    @classmethod
    def log_quantity_change(
        cls, order_item, previous_quantity, new_quantity, user=None, reason=None
    ):
        description = (
            f"Quantity changed from {previous_quantity} to {new_quantity}"
        )
        if reason:
            description += f". Reason: {reason}"

        return cls.objects.create(
            order_item=order_item,
            user=user,
            change_type="QUANTITY",
            previous_value={"quantity": previous_quantity},
            new_value={"quantity": new_quantity},
            description=description,
        )

    @classmethod
    def log_price_update(
        cls, order_item, previous_price, new_price, user=None, reason=None
    ):
        description = f"Price updated from {previous_price} to {new_price}"
        if reason:
            description += f". Reason: {reason}"

        previous_value = {
            "price": float(previous_price.amount),
            "currency": str(previous_price.currency),
        }

        new_value = {
            "price": float(new_price.amount),
            "currency": str(new_price.currency),
        }

        return cls.objects.create(
            order_item=order_item,
            user=user,
            change_type="PRICE",
            previous_value=previous_value,
            new_value=new_value,
            description=description,
        )

    @classmethod
    def log_refund(cls, order_item, refund_quantity, user=None, reason=None):
        description = f"Refund processed for {refund_quantity} items"
        if reason:
            description += f". Reason: {reason}"

        refund_amount = order_item.price.amount * refund_quantity

        return cls.objects.create(
            order_item=order_item,
            user=user,
            change_type="REFUND",
            new_value={
                "refund_quantity": refund_quantity,
                "refund_amount": float(refund_amount),
                "currency": str(order_item.price.currency),
            },
            description=description,
        )
