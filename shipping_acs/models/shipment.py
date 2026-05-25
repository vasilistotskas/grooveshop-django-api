from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from djmoney.money import Money
from simple_history.models import HistoricalRecords

from core.models import TimeStampMixinModel, UUIDModel
from shipping.enum import ShippingKind
from shipping_acs.enum.charge_type import AcsChargeType
from shipping_acs.enum.cod_payment_way import AcsCodPaymentWay
from shipping_acs.enum.shipment_state import AcsShipmentState

# States after which the order is no longer in flight — the polling
# task skips these to avoid burning ACS rate limits on settled rows.
_TERMINAL_STATES = frozenset(
    {
        AcsShipmentState.DELIVERED,
        AcsShipmentState.RETURNED,
        AcsShipmentState.CANCELED,
        AcsShipmentState.LOST,
    }
)


class AcsShipment(UUIDModel, TimeStampMixinModel):
    """One-to-one record linking an Order to an ACS voucher.

    Created with ``shipment_state=PENDING_CREATION`` at order-creation
    time.  After the Celery task succeeds, ``voucher_no`` is populated
    and ``Order.tracking_number`` is updated via ``add_tracking_info``.

    ``station_destination_external_id`` is denormalised so a deleted
    ``AcsStation`` row never orphans the shipment.
    """

    id = models.BigAutoField(primary_key=True)

    order = models.OneToOneField(
        "order.Order",
        on_delete=models.CASCADE,
        related_name="acs_shipment",
        verbose_name=_("Order"),
    )

    # ── ACS identifiers ──────────────────────────────────────────────
    # null=True (not blank=""): Postgres treats NULL as distinct in
    # UNIQUE constraints, so multiple "pending creation" rows can
    # coexist before the ACS API assigns real numbers (matches the
    # BoxNow pattern at shipping_boxnow/models/shipment.py).
    voucher_no = models.CharField(
        _("Voucher number"),
        max_length=20,
        unique=True,
        null=True,
        blank=True,
        default=None,
        help_text=_(
            "10-digit ACS voucher (Voucher_No from ACS_Create_Voucher)."
        ),
    )

    # ── Pickup list (daily manifest) ────────────────────────────────
    pickup_list = models.ForeignKey(
        "shipping_acs.AcsPickupList",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shipments",
        verbose_name=_("Pickup list"),
    )

    # ── Destination (Phase 2 = locker; Phase 1 always None) ─────────
    station_destination = models.ForeignKey(
        "shipping_acs.AcsStation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shipments",
        verbose_name=_("Destination station"),
    )
    station_destination_external_id = models.CharField(
        _("Destination station external ID"),
        max_length=32,
        blank=True,
        default="",
        help_text=_(
            "Denormalised ACS_SHOP_STATION_ID — preserved even if the "
            "AcsStation row is deleted."
        ),
    )
    station_branch_destination = models.CharField(
        _("Destination branch"),
        max_length=32,
        blank=True,
        default="",
        help_text=_("Acs_Station_Branch_Destination value."),
    )
    delivery_kind = models.CharField(
        _("Delivery kind"),
        max_length=32,
        choices=ShippingKind.choices,
        default=ShippingKind.HOME_DELIVERY,
    )

    # ── State + parcel parameters ───────────────────────────────────
    shipment_state = models.CharField(
        _("Shipment state"),
        max_length=32,
        choices=AcsShipmentState.choices,
        default=AcsShipmentState.PENDING_CREATION,
    )
    weight_grams = models.PositiveIntegerField(
        _("Weight (grams)"),
        default=0,
        help_text=_(
            "Internal grams; converted to kilograms (>= 0.5) at API call "
            "time per ACS Weight requirements."
        ),
    )
    item_quantity = models.PositiveSmallIntegerField(
        _("Item quantity"),
        default=1,
    )

    # ── Charge / COD ────────────────────────────────────────────────
    # Default is COD, matching the carrier-level default in
    # ``shipping_acs.carrier.AcsCarrier.create_shipment_row``. Our
    # ACS commercial contract is COD-only — PREPAID requests are
    # rejected with "Μη αποδεκτή τιμή χρέωσης μεταφορικών". Keeping
    # the model default in lockstep with the carrier default means a
    # future code path that creates an ``AcsShipment`` without
    # explicitly passing ``charge_type`` (an easy mistake) still
    # produces a working voucher — defence-in-depth alongside the
    # serializer field (no default) and the carrier method (COD
    # fallback). See orders 53/55/56 and memory
    # ``project_acs_contract_cod_only`` for the original incident.
    charge_type = models.PositiveSmallIntegerField(
        _("Charge type"),
        choices=AcsChargeType.choices,
        default=AcsChargeType.COD,
    )
    cod_amount = MoneyField(
        _("COD amount"),
        max_digits=11,
        decimal_places=2,
        default=Money(0, "EUR"),
        default_currency="EUR",
        help_text=_(
            "Amount collected at delivery. Required when "
            "``charge_type`` = COD; ignored otherwise."
        ),
    )
    cod_payment_way = models.PositiveSmallIntegerField(
        _("COD payment way"),
        choices=AcsCodPaymentWay.choices,
        null=True,
        blank=True,
    )
    delivery_products = models.CharField(
        _("Delivery products"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "Comma-separated Acs_Delivery_Products codes "
            "(COD, REC, SAT, RDO …)."
        ),
    )

    # ── Tracking poller bookkeeping ─────────────────────────────────
    delivery_flag = models.CharField(
        _("Delivery flag"),
        max_length=4,
        blank=True,
        default="",
        help_text=_("Raw delivery_flag value from ACS_Trackingsummary."),
    )
    returned_flag = models.CharField(
        _("Returned flag"),
        max_length=4,
        blank=True,
        default="",
    )
    raw_shipment_status = models.CharField(
        _("Raw shipment status"),
        max_length=8,
        blank=True,
        default="",
    )
    delivery_date = models.DateTimeField(
        _("Delivery date"), null=True, blank=True
    )
    last_polled_at = models.DateTimeField(
        _("Last polled at"),
        null=True,
        blank=True,
        help_text=_(
            "Used by poll_acs_tracking_batch to spread load and skip "
            "shipments polled within the last 15 minutes."
        ),
    )
    last_event_at = models.DateTimeField(
        _("Last event at"), null=True, blank=True
    )
    cancel_requested_at = models.DateTimeField(
        _("Cancel requested at"), null=True, blank=True
    )

    metadata = models.JSONField(
        _("Metadata"),
        default=dict,
        blank=True,
        help_text=_(
            "Multipart child voucher numbers, last create-voucher "
            "response, last error envelope, cached POD URL."
        ),
    )

    # Audit trail for voucher mint, state transitions, cancellation,
    # COD reconciliation. Mirrors product.Product.history. Listed
    # ``excluded_fields`` to keep history rows lean — the metadata
    # bag and the polling timestamps churn frequently and aren't
    # part of the audit story.
    history = HistoricalRecords(
        excluded_fields=["metadata", "last_polled_at", "updated_at"],
    )

    class Meta(TypedModelMeta):
        verbose_name = _("ACS shipment")
        verbose_name_plural = _("ACS shipments")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["voucher_no"], name="acs_shipment_voucher_ix"),
            BTreeIndex(fields=["shipment_state"], name="acs_shipment_state_ix"),
            BTreeIndex(fields=["pickup_list"], name="acs_shipment_pickup_ix"),
            BTreeIndex(
                fields=["last_polled_at"], name="acs_shipment_polled_ix"
            ),
            BTreeIndex(
                fields=["shipment_state", "last_polled_at"],
                name="acs_shipment_state_polled_ix",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"AcsShipment(order={self.order_id}, "
            f"voucher={self.voucher_no or 'pending'})"
        )

    @property
    def is_active(self) -> bool:
        """Return True when the shipment has not yet reached terminal state."""
        return self.shipment_state not in _TERMINAL_STATES
