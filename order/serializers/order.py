from django.utils.translation import gettext_lazy as _
from djmoney.contrib.django_rest_framework import MoneyField
from drf_spectacular.utils import extend_schema_field
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers
from rest_framework.relations import PrimaryKeyRelatedField

from core.utils.email import is_disposable_domain
from country.models import Country
from order.enum.document_type import OrderCreateDocumentTypeEnum
from order.enum.status import OrderStatus
from order.models.item import OrderItem
from order.models.order import Order
from order.serializers.item import (
    OrderItemCreateSerializer,
    OrderItemDetailSerializer,
)
from pay_way.models import PayWay
from product.models.product import Product
from region.models import Region
from shipping_acs.serializers.shipment import AcsShipmentDetailSerializer
from shipping_boxnow.serializers.shipment import (
    BoxNowShipmentDetailSerializer,
)

CARRIER_TRACKING_URLS: dict[str, str] = {
    "elta": "https://www.elta.gr/en/tracking?code={number}",
    "acs": "https://www.acscourier.net/el/track-and-trace/?p={number}",
    "speedex": "https://www.speedex.gr/en/track-and-trace/?p_code={number}",
    "dhl": "https://www.dhl.com/en/express/tracking.html?AWB={number}",
    "fedex": "https://www.fedex.com/fedextrack/?trknbr={number}",
}


class OrderSerializer(serializers.ModelSerializer[Order]):
    items = OrderItemDetailSerializer(many=True)
    country = PrimaryKeyRelatedField(
        queryset=Country.objects.all(), allow_null=True
    )
    region = PrimaryKeyRelatedField(
        queryset=Region.objects.all(), allow_null=True
    )
    pay_way = PrimaryKeyRelatedField(
        queryset=PayWay.objects.all(), allow_null=True
    )
    paid_amount = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    shipping_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    payment_method_fee = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )
    total_price_items = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )
    total_price_extra = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )
    phone = PhoneNumberField()
    status_display = serializers.SerializerMethodField("get_status_display")
    payment_status_display = serializers.SerializerMethodField(
        "get_payment_status_display",
        help_text=(
            "Localised label for ``payment_status`` (mirrors "
            "``status_display``). Frontend renders this rather than the "
            "raw enum value so Greek/English/German locales all work "
            "without per-locale string maps in the UI."
        ),
    )
    is_online_payment = serializers.SerializerMethodField(
        help_text=(
            "True when the order's PayWay charges the shopper online "
            "(Stripe, Viva); false for cash-on-delivery / bank "
            "transfer. Surfaced on both list + detail so both views "
            "can suppress misleading 'outstanding amount' warnings "
            "for COD orders where the shopper intentionally paid €0 "
            "at checkout."
        ),
    )
    can_be_canceled = serializers.BooleanField(read_only=True)
    is_paid = serializers.BooleanField(read_only=True)

    @extend_schema_field({"type": "string"})
    def get_status_display(self, order: Order) -> str:
        return order.get_status_display()

    @extend_schema_field({"type": "string"})
    def get_payment_status_display(self, order: Order) -> str:
        return order.get_payment_status_display()

    @extend_schema_field({"type": "boolean"})
    def get_is_online_payment(self, order: Order) -> bool:
        pay_way = getattr(order, "pay_way", None)
        return bool(pay_way and pay_way.is_online_payment)

    class Meta:
        model = Order
        fields = (
            "id",
            "user",
            "country",
            "region",
            "floor",
            "location_type",
            "street",
            "street_number",
            "pay_way",
            "status",
            "status_display",
            "status_updated_at",
            "first_name",
            "last_name",
            "email",
            "zipcode",
            "place",
            "city",
            "phone",
            "customer_notes",
            "paid_amount",
            "items",
            "shipping_price",
            "payment_method_fee",
            "document_type",
            "created_at",
            "updated_at",
            "uuid",
            "total_price_items",
            "total_price_extra",
            "full_address",
            "payment_id",
            "payment_status",
            "payment_status_display",
            "payment_method",
            "is_online_payment",
            "can_be_canceled",
            "is_paid",
        )
        read_only_fields = (
            "id",
            "uuid",
            "paid_amount",
            "shipping_price",
            "payment_method_fee",
            "total_price_items",
            "total_price_extra",
            "created_at",
            "updated_at",
            "status_updated_at",
            "can_be_canceled",
            "is_paid",
            "is_online_payment",
        )


class OrderDetailSerializer(OrderSerializer):
    items = OrderItemDetailSerializer(many=True)
    order_timeline = serializers.SerializerMethodField(
        help_text="Order status timeline and history"
    )
    pricing_breakdown = serializers.SerializerMethodField(
        help_text="Detailed pricing breakdown"
    )
    tracking_details = serializers.SerializerMethodField(
        help_text="Tracking and shipping details"
    )
    has_invoice = serializers.SerializerMethodField(
        help_text=(
            "True when a PDF invoice has been generated — the frontend "
            "can show the download CTA without issuing a separate "
            "request to the invoice endpoint to find out."
        )
    )
    boxnow_shipment = serializers.SerializerMethodField(
        help_text=(
            "BoxNow shipment details when shipping_provider.code is "
            "'boxnow', else null."
        )
    )
    acs_shipment = serializers.SerializerMethodField(
        help_text=(
            "ACS shipment details when the order's shipping provider "
            "is ACS, else null."
        )
    )
    shipment = serializers.SerializerMethodField(
        help_text=(
            "Provider-agnostic shipment payload — frontends can read "
            "this single field instead of branching on boxnow_shipment "
            "/ acs_shipment.  Returns the active provider's detail "
            "serializer dict (shape depends on the provider) or null "
            "when no shipment exists."
        )
    )
    shipment_provider_code = serializers.SerializerMethodField(
        help_text=(
            "Identifier of the carrier handling this order — 'acs', "
            "'boxnow', or null when no provider is attached.  Lets "
            "frontends switch on a stable code instead of inspecting "
            "the shipment shape."
        )
    )
    cancellation = serializers.SerializerMethodField(
        help_text=(
            "Cancellation context for CANCELED orders — exposes the "
            "operator-supplied reason, timestamp, and shipment-cancel "
            "outcome from ``order.metadata['cancellation']``. Returns "
            "null when the order was not canceled. Internal flags from "
            "the metadata bag (webhook idempotency markers, mint "
            "tickets) are intentionally not surfaced."
        )
    )
    meta_event_ids = serializers.SerializerMethodField(
        help_text=(
            "Meta Pixel ``eventID`` values the browser must reuse when "
            "firing the matching pixel call on the success page so "
            "Meta dedups the browser event against the server-side "
            "Conversions API event. Only the keys minted at order "
            "creation are surfaced (purchase, initiate_checkout, "
            "add_payment_info). Empty dict when the customer declined "
            "marketing cookies — in that case the browser should not "
            "fire the matching pixel either."
        )
    )
    currency = serializers.SerializerMethodField(
        help_text=(
            "ISO 4217 currency code for every monetary field on the "
            "order (paidAmount, shippingPrice, totalPriceItems, …). "
            "Surfaced as a top-level field because djmoney serialises "
            "money fields as bare numbers — without this, the "
            "frontend has no way to know whether ``59.98`` is EUR or "
            "USD, which breaks ad-pixel attribution and cart "
            "totals in multi-currency reports."
        )
    )
    phone = PhoneNumberField(read_only=True)

    @extend_schema_field(
        {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": (
                "Per-event-name UUIDs for Meta Pixel deduplication."
            ),
        }
    )
    def get_meta_event_ids(self, obj: Order) -> dict[str, str]:
        meta = obj.metadata or {}
        meta_ctx = meta.get("meta") or {}
        if not isinstance(meta_ctx, dict):
            return {}
        event_ids = meta_ctx.get("event_ids") or {}
        if not isinstance(event_ids, dict):
            return {}
        return {
            key: str(value)
            for key, value in event_ids.items()
            if isinstance(value, (str, int)) and str(value)
        }

    @extend_schema_field({"type": "string", "example": "EUR"})
    def get_currency(self, obj: Order) -> str:
        # ``paid_amount`` is the canonical reference: it's the field
        # the customer actually paid in. Falls back to total_price_items
        # for orders where paid_amount may be a zero Money (COD before
        # reconciliation), and finally to the project default. Walking
        # multiple sources keeps us safe against the rare case where
        # the order was created with one currency and reconciled in
        # another (shouldn't happen, but cheap to guard against).
        from django.conf import settings

        for source_name in (
            "paid_amount",
            "total_price_items",
            "shipping_price",
        ):
            money = getattr(obj, source_name, None)
            currency = getattr(money, "currency", None) if money else None
            if currency is not None:
                return str(currency).upper()
        return settings.DEFAULT_CURRENCY

    @extend_schema_field({"type": "boolean"})
    def get_has_invoice(self, obj: Order) -> bool:
        invoice = getattr(obj, "invoice", None)
        return bool(invoice and invoice.has_document())

    def _serialized_shipment(self, obj: Order) -> dict | None:
        """Compute the carrier shipment payload once per response.

        ``get_boxnow_shipment``, ``get_acs_shipment`` and the generic
        ``get_shipment`` all need the same dict. Without memoisation
        the carrier serializer ran twice per OrderDetail response (one
        carrier-specific call + the generic dispatcher).
        """
        cache: dict = self.context.setdefault("_shipment_cache", {})
        sentinel = object()
        cached = cache.get(obj.pk, sentinel)
        if cached is not sentinel:
            return cached
        from shipping.services import ShippingService

        result = ShippingService.serialize_shipment(obj, context=self.context)
        cache[obj.pk] = result
        return result

    @extend_schema_field(BoxNowShipmentDetailSerializer(allow_null=True))
    def get_boxnow_shipment(self, obj: Order) -> dict | None:
        # Mirror ``get_acs_shipment`` — gate on the registry-backed
        # ``shipping_provider`` FK rather than the denormalised
        # ``shipping_method`` enum so the field stays consistent
        # across carriers and the legacy column can be dropped.
        provider = getattr(obj, "shipping_provider", None)
        if provider is None or provider.code != "boxnow":
            return None
        return self._serialized_shipment(obj)

    @extend_schema_field(AcsShipmentDetailSerializer(allow_null=True))
    def get_acs_shipment(self, obj: Order) -> dict | None:
        provider = getattr(obj, "shipping_provider", None)
        if provider is None or provider.code != "acs":
            return None
        return self._serialized_shipment(obj)

    @extend_schema_field(
        {
            "type": "object",
            "nullable": True,
            "additionalProperties": True,
            "description": (
                "Provider-shipment detail payload. Shape varies by "
                "carrier — frontends should read shipmentProviderCode "
                "to decide which fields to render."
            ),
        }
    )
    def get_shipment(self, obj: Order) -> dict | None:
        """Generic provider-agnostic shipment payload.

        Dispatches via ``ShippingService.serialize_shipment(order)``
        which looks up the order's carrier adapter and returns its
        detail-serializer dict.  Frontends migrating off the
        provider-specific fields (``boxnow_shipment`` /
        ``acs_shipment``) should consume this one.
        """
        return self._serialized_shipment(obj)

    @extend_schema_field({"type": "string", "nullable": True})
    def get_shipment_provider_code(self, obj: Order) -> str | None:
        provider = getattr(obj, "shipping_provider", None)
        return provider.code if provider is not None else None

    @extend_schema_field(
        {
            "type": "object",
            "nullable": True,
            "properties": {
                "reason": {"type": "string"},
                "canceled_at": {"type": "string", "format": "date-time"},
                "previous_status": {"type": "string"},
                "shipment_cancel": {
                    "type": "object",
                    "nullable": True,
                    "properties": {
                        "attempted": {"type": "boolean"},
                        "dispatched": {"type": "boolean"},
                        "error": {"type": "string", "nullable": True},
                    },
                },
            },
        }
    )
    def get_cancellation(self, obj: Order) -> dict | None:
        meta = obj.metadata or {}
        cancellation = meta.get("cancellation")
        if not isinstance(cancellation, dict):
            return None
        out: dict[str, object] = {}
        for key in ("reason", "canceled_at", "previous_status"):
            value = cancellation.get(key)
            if value is not None:
                out[key] = value
        shipment_cancel = cancellation.get("shipment_cancel")
        if isinstance(shipment_cancel, dict):
            out["shipment_cancel"] = {
                "attempted": bool(shipment_cancel.get("attempted")),
                "dispatched": bool(shipment_cancel.get("dispatched")),
                "error": shipment_cancel.get("error"),
            }
        return out or None

    @extend_schema_field(
        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "change_type": {"type": "string"},
                    "timestamp": {"type": "string"},
                    "description": {"type": "string"},
                    "user": {"type": "string", "nullable": True},
                    "previous_value": {"type": "object", "nullable": True},
                    "new_value": {"type": "object", "nullable": True},
                },
            },
        }
    )
    def get_order_timeline(self, obj):
        timeline = []

        timeline.append(
            {
                "change_type": "CREATED",
                "timestamp": obj.created_at,
                "description": _("Order was created"),
                "user": None,
                "previous_value": None,
                "new_value": None,
            }
        )

        # Uses prefetched data from for_detail() — no extra query
        history_records = obj.history.all()

        for history in history_records:
            timeline.append(
                {
                    "change_type": history.change_type,
                    "timestamp": history.created_at,
                    "description": history.description,
                    "user": history.user.full_name if history.user else None,
                    "previous_value": history.previous_value,
                    "new_value": history.new_value,
                }
            )

        return timeline

    @extend_schema_field(
        {
            "type": "object",
            "properties": {
                "items_subtotal": {"type": "number"},
                "shipping_cost": {"type": "number"},
                "payment_method_fee": {"type": "number"},
                "extras_total": {"type": "number"},
                "grand_total": {"type": "number"},
                "currency": {"type": "string"},
                "paid_amount": {"type": "number"},
                "remaining_amount": {"type": "number"},
            },
        }
    )
    def get_pricing_breakdown(self, obj) -> dict:
        items_total = (
            obj.total_price_items.amount if obj.total_price_items else 0
        )
        shipping_total = obj.shipping_price.amount if obj.shipping_price else 0
        payment_fee = (
            obj.payment_method_fee.amount if obj.payment_method_fee else 0
        )
        extras_total = (
            obj.total_price_extra.amount if obj.total_price_extra else 0
        )

        grand_total = items_total + shipping_total + payment_fee

        return {
            "items_subtotal": items_total,
            "shipping_cost": shipping_total,
            "payment_method_fee": payment_fee,
            "extras_total": extras_total,
            "grand_total": grand_total,
            "currency": obj.total_price_items.currency.code
            if obj.total_price_items
            else "EUR",
            "paid_amount": obj.paid_amount.amount if obj.paid_amount else 0,
            "remaining_amount": max(
                grand_total
                - (obj.paid_amount.amount if obj.paid_amount else 0),
                0,
            ),
        }

    @extend_schema_field(
        {
            "type": "object",
            "nullable": True,
            "properties": {
                "tracking_number": {
                    "type": "string",
                    "nullable": True,
                },
                "shipping_carrier": {
                    "type": "string",
                    "nullable": True,
                },
                "has_tracking": {"type": "boolean"},
                "estimated_delivery": {
                    "type": "string",
                    "nullable": True,
                },
                "tracking_url": {
                    "type": "string",
                    "nullable": True,
                },
            },
        }
    )
    def get_tracking_details(self, obj) -> dict | None:
        tracking_url = None
        if obj.tracking_number and obj.shipping_carrier:
            template = CARRIER_TRACKING_URLS.get(obj.shipping_carrier.lower())
            if template:
                tracking_url = template.format(number=obj.tracking_number)

        return {
            "tracking_number": obj.tracking_number,
            "shipping_carrier": obj.shipping_carrier,
            "has_tracking": bool(obj.tracking_number),
            "estimated_delivery": None,
            "tracking_url": tracking_url,
        }

    class Meta(OrderSerializer.Meta):
        fields = (
            *OrderSerializer.Meta.fields,
            "items",
            "country",
            "region",
            "pay_way",
            "order_timeline",
            "pricing_breakdown",
            "tracking_details",
            "has_invoice",
            "boxnow_shipment",
            "acs_shipment",
            "shipment",
            "shipment_provider_code",
            "cancellation",
            "phone",
            "document_type",
            "payment_id",
            "payment_status",
            "payment_method",
            "tracking_number",
            "shipping_carrier",
            "customer_full_name",
            "is_completed",
            "is_canceled",
            "full_address",
            "meta_event_ids",
            "currency",
        )
        read_only_fields = (
            *OrderSerializer.Meta.read_only_fields,
            "order_timeline",
            "pricing_breakdown",
            "tracking_details",
            "has_invoice",
            "boxnow_shipment",
            "acs_shipment",
            "shipment",
            "shipment_provider_code",
        )


class OrderCreateFromCartSerializer(serializers.Serializer):
    """
    Serializer for creating orders from cart (dual-flow payment architecture).

    This serializer supports two payment flows:
    1. Online payments (is_online_payment=True): Requires payment_intent_id
    2. Offline payments (is_online_payment=False): No payment_intent_id required

    The order is created from an existing cart identified via X-Cart-Id header.
    Cart is NOT sent in request body - it's retrieved from the header using CartService.
    """

    # Payment method (required for all flows)
    pay_way_id = serializers.IntegerField(
        required=True, help_text=_("Payment method ID")
    )

    # Payment intent (required only for online payments)
    payment_intent_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text=_(
            "Payment intent ID from payment provider (required for online payments)"
        ),
    )

    # Shipping address fields (required for all flows)
    first_name = serializers.CharField(
        max_length=150, required=True, help_text=_("Customer first name")
    )
    last_name = serializers.CharField(
        max_length=150, required=True, help_text=_("Customer last name")
    )
    email = serializers.EmailField(
        required=True, help_text=_("Customer email address")
    )
    street = serializers.CharField(
        max_length=255, required=True, help_text=_("Street name")
    )
    street_number = serializers.CharField(
        max_length=50,
        required=False,
        allow_blank=True,
        help_text=_("Street number"),
    )
    city = serializers.CharField(
        max_length=100, required=True, help_text=_("City name")
    )
    zipcode = serializers.CharField(
        max_length=20, required=True, help_text=_("Postal/ZIP code")
    )
    country_id = serializers.CharField(
        required=True, help_text=_("Country alpha-2 code (e.g., 'GR', 'US')")
    )
    region_id = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text=_("Region alpha code"),
    )
    phone = PhoneNumberField(
        required=True, help_text=_("Customer phone number")
    )
    customer_notes = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
        help_text=_("Customer notes or special instructions"),
    )
    floor = serializers.CharField(
        max_length=50,
        required=False,
        allow_blank=True,
        help_text=_("Floor number or label (e.g. FIRST_FLOOR)"),
    )
    place = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text=_("Place or district (optional)"),
    )
    location_type = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        help_text=_("Location type, e.g. HOME or OFFICE (optional)"),
    )

    # B2B billing identity — required only when the buyer wants a
    # proper Τιμολόγιο Πώλησης (document_type=INVOICE). Normalised and
    # cross-validated in ``validate()`` so the Order row always has a
    # consistent (document_type, VAT) pair.
    billing_vat_id = serializers.CharField(
        max_length=12,
        required=False,
        allow_blank=True,
        help_text=_(
            "Buyer tax number (ΑΦΜ). Required when ``document_type`` "
            "is INVOICE; 9 digits for Greek ΑΦΜ, leading EL/GR "
            "prefix is stripped automatically."
        ),
    )
    billing_country = serializers.CharField(
        max_length=2,
        required=False,
        allow_blank=True,
        help_text=_(
            "ISO 3166-1 alpha-2 country code for the buyer's tax "
            "identity. Defaults to the order country when blank."
        ),
    )
    document_type = serializers.ChoiceField(
        choices=OrderCreateDocumentTypeEnum.choices,
        required=False,
        default=OrderCreateDocumentTypeEnum.RECEIPT,
        help_text=_(
            "RECEIPT (Α.Λ.Π., Tier A — retail) or INVOICE (Τιμολόγιο "
            "Πώλησης, Tier B — B2B). Selecting INVOICE requires a "
            "valid ``billing_vat_id``."
        ),
    )

    # Loyalty points redemption (optional)
    loyalty_points_to_redeem = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=0,
        help_text=_(
            "Number of loyalty points to redeem for discount on this order"
        ),
    )

    # BoxNow locker fields (required when carrier=boxnow + kind=pickup_point)
    boxnow_locker_id = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        help_text=_("BoxNow APM locker ID from the widget"),
    )
    boxnow_compartment_size = serializers.IntegerField(
        min_value=1,
        max_value=3,
        required=False,
        default=1,
        help_text=_("BoxNow compartment size: 1=Small, 2=Medium, 3=Large"),
    )

    # Shipping abstraction: ``(shipping_provider_code, shipping_kind)``
    # is the single source of truth for carrier dispatch. Validated
    # against the in-memory carrier registry; the dynamic home-delivery
    # auto-router fills ``shipping_provider`` server-side when callers
    # send only ``shipping_kind="home_delivery"``.
    shipping_provider_code = serializers.SlugField(
        max_length=32,
        required=False,
        allow_blank=True,
        help_text=_("Carrier code from /api/v1/shipping/options (e.g. 'acs')."),
    )
    shipping_kind = serializers.ChoiceField(
        choices=[
            ("home_delivery", _("Home delivery")),
            ("pickup_point", _("Pickup point / locker")),
        ],
        required=False,
        help_text=_("Generic fulfilment kind, independent of provider."),
    )
    # ACS-specific fields, only honoured when shipping_provider_code='acs'.
    acs_station_external_id = serializers.CharField(
        max_length=32,
        required=False,
        allow_blank=True,
        help_text=_(
            "ACS Smartpoint / shop external ID (Phase 2 pickup-point flow)."
        ),
    )
    acs_station_branch = serializers.CharField(
        max_length=32,
        required=False,
        allow_blank=True,
        help_text=_("ACS_Station_Branch_Destination value."),
    )
    acs_charge_type = serializers.ChoiceField(
        choices=[(1, _("Prepaid")), (2, _("Cash on delivery"))],
        required=False,
        # No ``default=`` — when the field is absent, DRF omits it
        # from ``validated_data`` so the carrier's own default
        # (``AcsChargeType.COD`` in ``shipping_acs.carrier``) wins.
        # Our ACS contract is COD-only; PREPAID is rejected at the
        # API. Setting ``default=1`` here used to leak that PREPAID
        # past the carrier fix (orders 53, 55, 56). The field stays
        # as an *optional* admin override so an admin can flip an
        # individual order back to PREPAID if the contract ever
        # gets it enabled — but the platform-wide default lives in
        # the carrier code, not here.
        help_text=_(
            "Optional per-order ACS Charge_Type override. Leave "
            "unset to use the carrier-level default (COD on a "
            "COD-only contract). Setting this here only makes "
            "sense if the ACS commercial contract permits the "
            "chosen value — invalid combinations are rejected by "
            "ACS_Create_Voucher."
        ),
    )

    # ---------- Meta Conversions API context ----------
    # Forwarded by the Nuxt ``server/api/orders/index.post.ts`` proxy
    # at order creation. Persisted on ``order.metadata['meta']`` so
    # the server-side Purchase/InitiateCheckout/Refund dispatchers
    # can build a ``UserData`` payload with the same fbp/fbc cookies
    # the browser pixel saw — the *only* way to reach a high
    # Event Match Quality score on Meta's side. Strictly write-only;
    # never serialised on Order detail responses.
    meta = serializers.DictField(
        required=False,
        allow_null=True,
        write_only=True,
        help_text=_(
            "Meta Pixel context: keys ``fbp``, ``fbc``, "
            "``client_user_agent``, ``client_ip_address``, "
            "``event_ids`` (dict of {purchase, initiate_checkout, "
            "add_payment_info}), ``consent`` (dict with ``ads`` "
            "boolean). Empty dict / null when the customer declined "
            "marketing cookies; the CAPI dispatcher then skips the "
            "send."
        ),
    )

    def validate_email(self, value: str) -> str:
        """Validate email is not from disposable domain."""
        if not value:
            raise serializers.ValidationError(_("Email is required."))

        email_domain = value.split("@")[-1]
        if is_disposable_domain(email_domain):
            raise serializers.ValidationError(
                _("Try using a different email address.")
            )
        return value

    def validate_billing_vat_id(self, value: str) -> str:
        """Strip ``EL`` / ``GR`` prefix then enforce 9-digit Greek ΑΦΜ.

        The prefix is VIES-convention but AADE's ``vatNumber`` field
        is unprefixed (error 104 otherwise). Normalising at the API
        boundary means both the admin and the PDF see the canonical
        value and we don't scatter normalisation across the pipeline.
        """
        if not value:
            return ""
        cleaned = value.strip().upper()
        if cleaned.startswith(("EL", "GR")):
            cleaned = cleaned[2:].strip()
        if not cleaned.isdigit() or len(cleaned) != 9:
            raise serializers.ValidationError(
                _(
                    "Greek ΑΦΜ must be exactly 9 digits "
                    "(optionally prefixed with EL or GR)."
                )
            )
        return cleaned

    def validate_billing_country(self, value: str) -> str:
        """Normalise to uppercase ISO-alpha2; allow empty."""
        if not value:
            return ""
        cleaned = value.strip().upper()
        if len(cleaned) != 2 or not cleaned.isalpha():
            raise serializers.ValidationError(
                _("Country must be a 2-letter ISO code (e.g. GR).")
            )
        return cleaned

    def validate(self, attrs):
        """Cross-field rules for B2B invoicing and BoxNow shipping.

        1. ``B2B_INVOICING_ENABLED`` gates the feature site-wide — when
           off, ``document_type=INVOICE`` is rejected so the API can't
           be bypassed via direct calls while the UI hides the toggle.
        2. ``document_type=INVOICE`` ⇒ ``billing_vat_id`` required.
           Otherwise the myDATA submission would silently downgrade to
           11.1 (tax-fraud-adjacent) or hard-fail at the worker.
        3. ``(shipping_provider_code='boxnow', shipping_kind='pickup_point')``
           ⇒ ``boxnow_locker_id`` required and ``pay_way`` must be an
           online-payment method (BoxNow rejects COD at lockers).
        """
        from extra_settings.models import Setting

        document_type = attrs.get("document_type", "RECEIPT")
        billing_vat_id = attrs.get("billing_vat_id", "")
        if document_type == "INVOICE" and not Setting.get(
            "B2B_INVOICING_ENABLED", default=True
        ):
            raise serializers.ValidationError(
                {"document_type": _("B2B invoicing is currently disabled.")}
            )
        if document_type == "INVOICE" and not billing_vat_id:
            raise serializers.ValidationError(
                {
                    "billing_vat_id": _(
                        "A valid ΑΦΜ is required when requesting an "
                        "invoice (document_type=INVOICE)."
                    )
                }
            )

        # BoxNow cross-field validation. Trigger condition is the
        # registry-driven ``(shipping_provider_code, shipping_kind)``
        # pair — the legacy ``shipping_method`` enum no longer drives
        # carrier selection.
        is_boxnow_pickup = (
            attrs.get("shipping_provider_code") == "boxnow"
            and attrs.get("shipping_kind") == "pickup_point"
        )
        if is_boxnow_pickup:
            # Master switch — admin can hide BoxNow without redeploy.
            # Production starts disabled (BOXNOW_ENABLED defaults to
            # False); we only allow BoxNow locker orders once an admin
            # has flipped the Setting row to True. Defends against a
            # stale frontend cache surfacing the option.
            if not Setting.get("BOXNOW_ENABLED", default=False):
                raise serializers.ValidationError(
                    {
                        "shipping_provider_code": _(
                            "BoxNow locker shipping is currently unavailable."
                        )
                    }
                )
            if not attrs.get("boxnow_locker_id"):
                raise serializers.ValidationError(
                    {
                        "boxnow_locker_id": _(
                            "Locker ID required when shipping method is BoxNow"
                        )
                    }
                )

        return attrs


class OrderWriteSerializer(serializers.ModelSerializer[Order]):
    items = OrderItemCreateSerializer(many=True)
    paid_amount = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    shipping_price = MoneyField(max_digits=11, decimal_places=2, read_only=True)
    payment_method_fee = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )
    total_price_items = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )
    total_price_extra = MoneyField(
        max_digits=11, decimal_places=2, read_only=True
    )
    phone = PhoneNumberField()

    def validate_items(self, value: list[dict]) -> list[dict]:
        if not value:
            raise serializers.ValidationError(
                _("At least one item is required.")
            )

        for item_data in value:
            if item_data.get("quantity", 0) <= 0:
                raise serializers.ValidationError(
                    _("Item quantity must be greater than zero.")
                )

        return value

    def validate_email(self, value: str) -> str:
        if not value:
            raise serializers.ValidationError(_("Email is required."))

        email_domain = value.split("@")[-1]
        if is_disposable_domain(email_domain):
            raise serializers.ValidationError(
                _("Try using a different email address.")
            )
        return value

    def validate(self, attrs):
        items_data = attrs.get("items", [])

        # Batch-fetch all products in a single query to avoid N+1.
        product_ids = []
        for item_data in items_data:
            product = item_data.get("product")
            pid = product.id if hasattr(product, "id") else product
            product_ids.append(pid)

        products_map = {
            p.pk: p for p in Product.objects.filter(pk__in=product_ids)
        }

        for item_data in items_data:
            raw = item_data.get("product")
            product_id = raw.id if hasattr(raw, "id") else raw
            quantity = item_data.get("quantity", 0)

            product = products_map.get(product_id)
            if product is None:
                raise serializers.ValidationError(
                    _("Product with id '{product_id}' does not exist.").format(
                        product_id=product_id
                    )
                )

            if not product.active:
                raise serializers.ValidationError(
                    _(
                        "Product with id '{product_name}' is not available."
                    ).format(
                        product_name=product.safe_translation_getter(
                            "name", any_language=True
                        )
                    )
                )

            if product.stock < quantity:
                raise serializers.ValidationError(
                    _(
                        "Not enough stock for '{product_name}'."
                        " Available: {product_stock}, Requested: {quantity}"
                    ).format(
                        product_name=product.safe_translation_getter(
                            "name", any_language=True
                        ),
                        product_stock=product.stock,
                        quantity=quantity,
                    )
                )

        return attrs

    def create(self, validated_data):
        from django.conf import settings
        from djmoney.money import Money
        from extra_settings.models import Setting

        items_data = validated_data.pop("items")

        # Calculate items total and shipping
        items_total = Money(0, settings.DEFAULT_CURRENCY)
        for item_data in items_data:
            product = item_data.get("product")
            quantity = item_data.get("quantity", 1)
            items_total += product.final_price * quantity

        base_shipping_cost = Setting.get(
            "CHECKOUT_SHIPPING_PRICE", default=3.00
        )
        free_shipping_threshold = Setting.get(
            "FREE_SHIPPING_THRESHOLD", default=50.00
        )

        if items_total.amount >= float(free_shipping_threshold):
            shipping_price = Money(0, items_total.currency)
        else:
            shipping_price = Money(
                float(base_shipping_cost), items_total.currency
            )

        validated_data["shipping_price"] = shipping_price

        # Store cart_id in order metadata for guest cart clearing
        request = self.context.get("request")
        if request and not validated_data.get("user"):
            cart_id = None
            if hasattr(request, "META"):
                cart_id = request.META.get("HTTP_X_CART_ID")
            elif hasattr(request, "headers"):
                cart_id = request.headers.get("X-Cart-Id")

            if cart_id:
                try:
                    cart_id = int(cart_id)
                    if "metadata" not in validated_data:
                        validated_data["metadata"] = {}
                    validated_data["metadata"]["cart_id"] = cart_id
                except (ValueError, TypeError):
                    pass

        # Validate and lock stock BEFORE creating order. Locking all
        # products in one ``SELECT … FOR UPDATE WHERE pk IN (…)`` is
        # one round-trip regardless of cart size — the previous
        # per-item loop fired N locks + N updates inside the same
        # transaction, scaling round-trips linearly with cart depth.
        from product.models import Product

        product_ids = [item["product"].pk for item in items_data]
        locked_products = {
            p.pk: p
            for p in Product.objects.select_for_update().filter(
                pk__in=product_ids
            )
        }

        for item_data in items_data:
            product = item_data.get("product")
            quantity = item_data.get("quantity", 1)
            locked_product = locked_products.get(product.pk)
            if locked_product is None:
                # Product disappeared between cart load and order create.
                raise serializers.ValidationError(
                    {"items": [f"Product {product.pk} no longer available."]}
                )

            if locked_product.stock < quantity:
                raise serializers.ValidationError(
                    {
                        "items": [
                            f"Product '{locked_product.safe_translation_getter('name', any_language=True)}' "
                            f"does not have enough stock. Available: {locked_product.stock}, "
                            f"Requested: {quantity}"
                        ]
                    }
                )

            # Deduct stock immediately in transaction
            locked_product.stock = max(0, locked_product.stock - quantity)
            locked_product.save(update_fields=["stock"])

        # Create order after stock is validated and deducted
        order = Order.objects.create(**validated_data)

        # Create order items (stock already deducted above)
        for item_data in items_data:
            product = item_data.get("product")
            item_to_create = item_data.copy()
            item_to_create["price"] = product.final_price

            # Set flag BEFORE creating to prevent signal from deducting again
            OrderItem._skip_stock_deduction = True
            OrderItem.objects.create(order=order, **item_to_create)
            OrderItem._skip_stock_deduction = False

        order.paid_amount = order.calculate_order_total_amount()
        order.save(update_fields=["paid_amount", "paid_amount_currency"])

        # Mark order to send signal after transaction commits
        order._send_created_signal = True

        return order

    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()

            for item_data in items_data:
                product = item_data.get("product")
                item_to_create = item_data.copy()
                item_to_create["price"] = product.final_price
                OrderItem.objects.create(order=instance, **item_to_create)

        return instance

    class Meta:
        model = Order
        fields = (
            "user",
            "country",
            "region",
            "floor",
            "location_type",
            "street",
            "street_number",
            "pay_way",
            "status",
            "first_name",
            "last_name",
            "email",
            "zipcode",
            "place",
            "city",
            "phone",
            "paid_amount",
            "customer_notes",
            "items",
            "shipping_price",
            "payment_method_fee",
            "total_price_items",
            "total_price_extra",
            "document_type",
            "payment_id",
            "payment_status",
            "payment_method",
            "tracking_number",
            "shipping_carrier",
        )
        read_only_fields = (
            "shipping_price",
            "payment_method_fee",
            "total_price_items",
            "total_price_extra",
            "status",
            "payment_id",
            "payment_status",
            "payment_method",
            "tracking_number",
            "shipping_carrier",
        )
        extra_kwargs = {
            "user": {
                "required": False,
                "allow_null": True,
                "help_text": _("User ID. Leave empty for guest orders."),
            }
        }


class AddTrackingSerializer(serializers.Serializer):
    tracking_number = serializers.CharField(max_length=100)
    shipping_carrier = serializers.CharField(max_length=50)


class UpdateStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=OrderStatus.choices)


class CreatePaymentIntentRequestSerializer(serializers.Serializer):
    payment_data = serializers.DictField(
        required=False,
        default=dict,
        child=serializers.CharField(max_length=500),
        help_text=_("Additional payment data required by the payment provider"),
    )


class CreatePaymentIntentResponseSerializer(serializers.Serializer):
    payment_id = serializers.CharField(
        help_text=_("Payment intent ID from the payment provider")
    )

    status = serializers.CharField(help_text=_("Payment status"))

    amount = serializers.CharField(help_text=_("Payment amount"))

    currency = serializers.CharField(help_text=_("Payment currency"))

    provider = serializers.CharField(help_text=_("Payment provider name"))

    client_secret = serializers.CharField(
        required=False,
        help_text=_(
            "Stripe PaymentIntent client secret for frontend confirmation"
        ),
    )

    requires_action = serializers.BooleanField(
        required=False,
        default=False,
        help_text=_(
            "Whether the payment requires additional action (3D Secure, etc.)"
        ),
    )

    next_action = serializers.DictField(
        required=False,
        allow_null=True,
        help_text=_("Next action required for payment completion"),
    )


class CreateCheckoutSessionRequestSerializer(serializers.Serializer):
    success_url = serializers.URLField(required=True)
    cancel_url = serializers.URLField(required=True)
    customer_email = serializers.EmailField(required=False)
    customer_id = serializers.CharField(required=False)
    description = serializers.CharField(required=False, max_length=500)


class CreateCheckoutSessionResponseSerializer(serializers.Serializer):
    session_id = serializers.CharField()
    checkout_url = serializers.URLField()
    status = serializers.CharField()
    amount = serializers.CharField()
    currency = serializers.CharField()
    provider = serializers.CharField()


class RefundOrderRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        allow_null=True,
        help_text=_("Partial refund amount. Leave empty for full refund."),
    )
    currency = serializers.CharField(
        max_length=3,
        required=False,
        allow_null=True,
        help_text=_(
            "Currency code (e.g., USD, EUR). Required if amount is provided."
        ),
    )
    reason = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
        help_text=_("Reason for the refund"),
    )

    def validate(self, attrs):
        amount = attrs.get("amount")
        currency = attrs.get("currency")

        if amount is not None and not currency:
            raise serializers.ValidationError(
                {"currency": _("Currency is required when amount is provided")}
            )

        if amount is not None and amount <= 0:
            raise serializers.ValidationError(
                {"amount": _("Refund amount must be greater than 0")}
            )

        return attrs


class RefundOrderResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    refund_id = serializers.CharField(required=False)
    status = serializers.CharField()
    amount = serializers.CharField()
    payment_id = serializers.CharField(required=False)
    stripe_status = serializers.CharField(required=False)
    error = serializers.CharField(required=False)
    message = serializers.CharField(required=False)


class PaymentStatusResponseSerializer(serializers.Serializer):
    payment_id = serializers.CharField()
    status = serializers.CharField()
    raw_status = serializers.CharField(required=False)
    provider = serializers.CharField()
    amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False
    )
    currency = serializers.CharField(required=False)
    created = serializers.IntegerField(required=False)
    last_updated = serializers.DateTimeField(required=False, allow_null=True)
    error = serializers.CharField(required=False)


class CancelOrderRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
        help_text="Reason for canceling the order",
    )
    refund_payment = serializers.BooleanField(
        default=True,
        help_text="Whether to automatically refund the payment if the order is paid",
    )


class ReorderItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    requested_quantity = serializers.IntegerField()
    added_quantity = serializers.IntegerField(required=False, default=0)
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class ReorderResponseSerializer(serializers.Serializer):
    cart_id = serializers.IntegerField(allow_null=True)
    added_items = ReorderItemSerializer(many=True)
    skipped_items = ReorderItemSerializer(many=True)
