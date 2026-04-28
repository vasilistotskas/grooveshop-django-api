from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from djmoney.money import Money

from core.models import TimeStampMixinModel, UUIDModel
from shipping_boxnow.enum.compartment_size import BoxNowCompartmentSize
from shipping_boxnow.enum.parcel_state import BoxNowParcelState
from shipping_boxnow.enum.payment_mode import BoxNowPaymentMode

_TERMINAL_STATES = frozenset(
    {
        BoxNowParcelState.DELIVERED,
        BoxNowParcelState.CANCELED,
        BoxNowParcelState.RETURNED,
        BoxNowParcelState.EXPIRED,
        BoxNowParcelState.LOST,
        BoxNowParcelState.MISSING,
    }
)


class BoxNowShipment(UUIDModel, TimeStampMixinModel):
    """
    One-to-one record linking an Order to a BoxNow delivery request.

    Created with ``parcel_state=PENDING_CREATION`` at order creation
    time.  After the Celery task calls the BoxNow API successfully,
    ``delivery_request_id`` and ``parcel_id`` are populated and
    ``Order.tracking_number`` is updated.

    ``locker_external_id`` is denormalised so that hard-deleting a
    ``BoxNowLocker`` row never orphans the shipment record.
    """

    id = models.BigAutoField(primary_key=True)

    order = models.OneToOneField(
        "order.Order",
        on_delete=models.CASCADE,
        related_name="boxnow_shipment",
        verbose_name=_("Order"),
    )
    locker = models.ForeignKey(
        "shipping_boxnow.BoxNowLocker",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shipments",
        verbose_name=_("Locker"),
    )

    # ── BoxNow identifiers ─────────────────────────────────────────
    # null=True (not blank=""): Postgres treats NULL as distinct in UNIQUE
    # constraints, so multiple "pending creation" rows can coexist before
    # the BoxNow API assigns real IDs. Empty strings would collide.
    delivery_request_id = models.CharField(
        _("Delivery Request ID"),
        max_length=128,
        unique=True,
        null=True,
        blank=True,
        default=None,
        help_text=_(
            "BoxNow delivery-request ID returned from POST "
            "/api/v1/delivery-requests"
        ),
    )
    parcel_id = models.CharField(
        _("Parcel ID"),
        max_length=32,
        unique=True,
        null=True,
        blank=True,
        default=None,
        help_text=_("10-digit BoxNow voucher number"),
    )
    locker_external_id = models.CharField(
        _("Locker External ID"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "Denormalised BoxNow APM ID — preserved even if the "
            "BoxNowLocker row is deleted"
        ),
    )

    # ── State & shipment config ────────────────────────────────────
    parcel_state = models.CharField(
        _("Parcel State"),
        max_length=30,
        choices=BoxNowParcelState.choices,
        default=BoxNowParcelState.PENDING_CREATION,
    )
    compartment_size = models.PositiveSmallIntegerField(
        _("Compartment Size"),
        choices=BoxNowCompartmentSize.choices,
        default=BoxNowCompartmentSize.SMALL,
    )
    weight_grams = models.PositiveIntegerField(
        _("Weight (grams)"),
        default=0,
    )
    payment_mode = models.CharField(
        _("Payment Mode"),
        max_length=10,
        choices=BoxNowPaymentMode.choices,
        default=BoxNowPaymentMode.PREPAID,
    )
    amount_to_be_collected = MoneyField(
        _("Amount to be Collected"),
        max_digits=11,
        decimal_places=2,
        default=Money(0, "EUR"),
        default_currency="EUR",
        help_text=_(
            "Amount collected at delivery (PoG / COD). Always 0 in Phase 1."
        ),
    )
    allow_return = models.BooleanField(
        _("Allow Return"),
        default=True,
    )

    # ── Tracking URLs / timestamps ─────────────────────────────────
    label_url = models.URLField(
        _("Label URL"),
        max_length=500,
        blank=True,
        default="",
        help_text=_("Cached BoxNow-hosted label URL if returned"),
    )
    last_event_at = models.DateTimeField(
        _("Last Event At"),
        null=True,
        blank=True,
    )
    cancel_requested_at = models.DateTimeField(
        _("Cancel Requested At"),
        null=True,
        blank=True,
    )

    # ── Diagnostics ────────────────────────────────────────────────
    metadata = models.JSONField(
        _("Metadata"),
        default=dict,
        blank=True,
        help_text=_(
            "Full delivery-request response and diagnostics from BoxNow"
        ),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("BoxNow Shipment")
        verbose_name_plural = _("BoxNow Shipments")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(
                fields=["parcel_state"],
                name="boxnow_shipment_state_ix",
            ),
            BTreeIndex(
                fields=["parcel_id"],
                name="boxnow_shipment_parcel_id_ix",
            ),
            BTreeIndex(
                fields=["delivery_request_id"],
                name="boxnow_shipment_req_id_ix",
            ),
            BTreeIndex(
                fields=["locker_external_id"],
                name="boxnow_shipment_locker_ext_ix",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"BoxNowShipment(order={self.order_id}, "
            f"parcel_id={self.parcel_id or 'pending'})"
        )

    @property
    def is_active(self) -> bool:
        """Return True if the shipment has not reached a terminal state."""
        return self.parcel_state not in _TERMINAL_STATES
