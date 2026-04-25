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
    can_be_canceled = serializers.BooleanField(read_only=True)
    is_paid = serializers.BooleanField(read_only=True)

    def get_status_display(self, order: Order) -> str:
        return order.get_status_display()

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
            "payment_method",
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
    phone = PhoneNumberField(read_only=True)

    @extend_schema_field({"type": "boolean"})
    def get_has_invoice(self, obj: Order) -> bool:
        invoice = getattr(obj, "invoice", None)
        return bool(invoice and invoice.has_document())

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
        )
        read_only_fields = (
            *OrderSerializer.Meta.read_only_fields,
            "order_timeline",
            "pricing_breakdown",
            "tracking_details",
            "has_invoice",
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
        """Cross-field rules for B2B invoicing.

        1. ``B2B_INVOICING_ENABLED`` gates the feature site-wide — when
           off, ``document_type=INVOICE`` is rejected so the API can't
           be bypassed via direct calls while the UI hides the toggle.
        2. ``document_type=INVOICE`` ⇒ ``billing_vat_id`` required.
           Otherwise the myDATA submission would silently downgrade to
           11.1 (tax-fraud-adjacent) or hard-fail at the worker.
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

    def validate(self, data):
        items_data = data.get("items", [])

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

        return data

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

        # Validate and lock stock BEFORE creating order
        from product.models import Product

        for item_data in items_data:
            product = item_data.get("product")
            quantity = item_data.get("quantity", 1)

            # Lock product row for update to prevent race conditions
            locked_product = Product.objects.select_for_update().get(
                pk=product.pk
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
