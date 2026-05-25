"""Unit tests for AcsCarrier — the registry adapter."""

from __future__ import annotations

import pytest

from shipping.enum import ShippingKind
from shipping.interfaces import get_provider, is_registered


def test_acs_adapter_registers_under_code_acs():
    assert is_registered("acs") is True
    adapter = get_provider("acs")
    assert adapter.code == "acs"


@pytest.mark.django_db
def test_acs_validation_requires_locker_for_pickup_point():
    adapter = get_provider("acs")
    errors = adapter.validate_order_payload(
        kind=ShippingKind.PICKUP_POINT,
        payload={},
    )
    assert "acs_station_external_id" in errors


@pytest.mark.django_db
def test_acs_validation_passes_for_home_delivery():
    adapter = get_provider("acs")
    assert (
        adapter.validate_order_payload(
            kind=ShippingKind.HOME_DELIVERY, payload={}
        )
        == {}
    )


@pytest.mark.django_db
def test_calculate_shipping_cost_uses_settings_threshold():
    adapter = get_provider("acs")

    # Below threshold → returns flat rate (default 3.50 from settings).
    cheap = adapter.calculate_shipping_cost(
        order_value_amount=10.0,
        currency="EUR",
        kind=ShippingKind.HOME_DELIVERY,
    )
    assert cheap is not None
    assert cheap[0] > 0

    # At/above threshold (default 40) → free shipping.
    free = adapter.calculate_shipping_cost(
        order_value_amount=80.0,
        currency="EUR",
        kind=ShippingKind.HOME_DELIVERY,
    )
    assert free == (0.0, "EUR")


@pytest.mark.django_db
def test_create_shipment_row_defaults_to_cod_charge_type():
    """Regression test for orders 53, 55, 56.

    Our ACS commercial contract rejects ``Charge_Type=PREPAID`` with
    "Μη αποδεκτή τιμή χρέωσης μεταφορικών" — the carrier MUST
    default to COD when the order payload doesn't supply an explicit
    override. Pre-fix, the serializer's ``default=1`` injected
    PREPAID into ``validated_data`` and the carrier's
    ``or AcsChargeType.COD`` fallback never fired because ``1`` is
    truthy. Three customers paid before this was caught — pin the
    contract here so the regression can't sneak back in via a stray
    serializer default.
    """
    from order.factories.order import OrderFactory
    from shipping_acs.enum.charge_type import AcsChargeType
    from shipping_acs.models import AcsShipment

    order = OrderFactory(shipping_kind=ShippingKind.HOME_DELIVERY.value)
    adapter = get_provider("acs")

    # Empty payload — no ``acs_charge_type`` override. Carrier
    # default MUST be COD.
    adapter.create_shipment_row(
        order, kind=ShippingKind.HOME_DELIVERY, payload={}
    )
    shipment = AcsShipment.objects.get(order=order)
    assert shipment.charge_type == AcsChargeType.COD, (
        "Empty payload must default to COD; got "
        f"{shipment.charge_type} ({AcsChargeType(shipment.charge_type).label})"
    )


@pytest.mark.django_db
def test_create_shipment_row_explicit_acs_charge_type_overrides():
    """The ``acs_charge_type`` admin override is still wired up.

    If the contract ever supports PREPAID (or any per-order
    deviation), an admin can pass an explicit value and the carrier
    must honor it. Asserts the override hook in
    ``create_shipment_row`` still respects the payload's explicit
    value even though COD is the platform default.
    """
    from order.factories.order import OrderFactory
    from shipping_acs.enum.charge_type import AcsChargeType
    from shipping_acs.models import AcsShipment

    order = OrderFactory(shipping_kind=ShippingKind.HOME_DELIVERY.value)
    adapter = get_provider("acs")

    adapter.create_shipment_row(
        order,
        kind=ShippingKind.HOME_DELIVERY,
        payload={"acs_charge_type": AcsChargeType.PREPAID},
    )
    shipment = AcsShipment.objects.get(order=order)
    assert shipment.charge_type == AcsChargeType.PREPAID


@pytest.mark.django_db
def test_order_create_serializer_omits_acs_charge_type_by_default():
    """The serializer must NOT inject a default for ``acs_charge_type``.

    Hard-pinning the upstream half of the regression that caused
    orders 53/55/56 to fail. If a future change adds ``default=X``
    back to the field, this test fails loud — without it, the
    serializer's default would leak past the carrier-level COD
    default again and ACS would silently reject every voucher.
    """
    from order.serializers.order import OrderCreateFromCartSerializer

    serializer = OrderCreateFromCartSerializer(data={})
    # We're not validating the whole order body here — just asserting
    # the field's contract. Inspect the field instance directly.
    field = serializer.fields["acs_charge_type"]
    assert field.required is False
    # ``serializers.empty`` is DRF's sentinel for "no default set".
    from rest_framework.fields import empty

    assert field.default is empty, (
        "OrderCreateFromCartSerializer.acs_charge_type must not have "
        "a default — DRF inserts it into validated_data, which would "
        "leak past the carrier-level COD default. See orders 53/55/56."
    )


@pytest.mark.django_db
def test_create_shipment_row_item_quantity_defaults_to_one():
    """Regression test for order 56 (cart with 3 items → 3 ACS vouchers).

    ACS ``Item_Quantity`` is the number of **physical parcels**, not
    cart line items. When the carrier sent
    ``Item_Quantity = sum(cart quantities)``, ACS treated the
    shipment as multi-parcel and ``get_multipart_vouchers`` auto-
    minted one child voucher per "piece" (parent + N-1 children).
    The shop physically bundles every order into one box, so the
    correct default is always ``1`` — the merchant only needs to
    print one label.

    Asserts: regardless of how many cart line items the order has
    (here we pass a fake 5-line ``items`` iterable), the resulting
    AcsShipment row carries ``item_quantity=1``.
    """
    from order.factories.order import OrderFactory
    from shipping_acs.models import AcsShipment

    order = OrderFactory(shipping_kind=ShippingKind.HOME_DELIVERY.value)
    adapter = get_provider("acs")

    # Five fake cart lines, each with quantity 2 — pre-fix this
    # would have produced ``item_quantity = 10``.
    fake_items = [(None, 2) for _ in range(5)]

    adapter.create_shipment_row(
        order,
        kind=ShippingKind.HOME_DELIVERY,
        payload={},
        items=fake_items,
    )
    shipment = AcsShipment.objects.get(order=order)
    assert shipment.item_quantity == 1, (
        f"Cart with multiple items must NOT produce multipart "
        f"vouchers — Item_Quantity must be 1; got "
        f"{shipment.item_quantity}. This is the order-56 regression."
    )


@pytest.mark.django_db
def test_create_shipment_row_item_quantity_admin_override():
    """Admin override hatch — ``acs_item_quantity`` payload key
    forces multi-parcel for merchants that genuinely ship one
    order in multiple boxes. The default stays 1 (see above);
    explicit override must still win.
    """
    from order.factories.order import OrderFactory
    from shipping_acs.models import AcsShipment

    order = OrderFactory(shipping_kind=ShippingKind.HOME_DELIVERY.value)
    adapter = get_provider("acs")
    adapter.create_shipment_row(
        order,
        kind=ShippingKind.HOME_DELIVERY,
        payload={"acs_item_quantity": 3},
    )
    shipment = AcsShipment.objects.get(order=order)
    assert shipment.item_quantity == 3


@pytest.mark.django_db
def test_order_create_serializer_omits_acs_item_quantity_by_default():
    """Same pin as ``acs_charge_type`` — the serializer field for
    ``acsItemQuantity`` MUST NOT carry a DRF ``default=``. Any value
    here would be injected into ``validated_data`` and short-circuit
    the carrier's "default to 1" logic via the truthy ``or 1``
    fallback. Tested explicitly so the PREPAID-leak pattern (commit
    ``c20af461``) can't recur for this field either.
    """
    from rest_framework.fields import empty

    from order.serializers.order import OrderCreateFromCartSerializer

    field = OrderCreateFromCartSerializer().fields["acs_item_quantity"]
    assert field.required is False
    assert field.default is empty, (
        "OrderCreateFromCartSerializer.acs_item_quantity must not "
        "have a default — see comment in the field declaration and "
        "the PREPAID-leak postmortem on acs_charge_type."
    )


@pytest.mark.django_db
def test_acs_shipment_model_default_is_cod():
    """Deepest defence-in-depth layer.

    If ALL upstream defaults fail (serializer regression + carrier
    regression + view regression), the Django model-level default
    is the last line. It must be COD so that even a code path that
    creates an ``AcsShipment`` row without explicitly passing
    ``charge_type`` (e.g. a hypothetical future admin form, a data
    migration, or an integration test) still yields a row whose
    voucher mint ACS will accept.
    """
    from shipping_acs.enum.charge_type import AcsChargeType
    from shipping_acs.models import AcsShipment

    field = AcsShipment._meta.get_field("charge_type")
    assert field.default == AcsChargeType.COD, (
        f"AcsShipment.charge_type model default must be COD; got "
        f"{field.default} ({AcsChargeType(field.default).label})."
    )


@pytest.mark.django_db
def test_validated_data_omits_acs_charge_type_when_absent_from_body():
    """End-to-end proof the regression is closed.

    Run the serializer's actual ``is_valid()`` against a realistic
    order-create body that does NOT include ``acsChargeType`` and
    assert the resulting ``validated_data`` dict OMITS the key
    entirely. This is the value the view passes to
    ``_build_shipping_address_from_validated`` → carrier payload —
    if it's absent, the carrier's COD default fires.

    Pre-fix (``default=1``): ``validated_data["acs_charge_type"] == 1``.
    Post-fix (``default=empty``): key absent, ``.get`` returns None,
    carrier picks ``AcsChargeType.COD``.

    Pairs with the model-level + override tests above. If those
    pass but this one fails, someone reintroduced a default upstream
    of the carrier without touching the serializer field itself
    (e.g. via ``DEFAULT_FIELD_DEFAULTS`` overrides or a parent
    serializer mixin).
    """
    from order.serializers.order import OrderCreateFromCartSerializer

    # Minimal-but-realistic body for the offline-payment create-from-
    # cart endpoint. ``acsChargeType`` deliberately absent — this is
    # what the storefront sends on every order.
    body = {
        "first_name": "Test",
        "last_name": "Customer",
        "email": "test@example.com",
        "phone": "+306900000000",
        "street": "Test Street",
        "street_number": "1",
        "city": "Athens",
        "zipcode": "11141",
        "country_id": "GR",
        "shipping_provider_code": "acs",
        "shipping_kind": "home_delivery",
        "document_type": "RECEIPT",
        "pay_way": 1,
    }

    serializer = OrderCreateFromCartSerializer(data=body)
    # ``raise_exception=False`` — other required fields might error
    # depending on which factories are seeded; we only care about
    # this one field's contract.
    serializer.is_valid(raise_exception=False)

    # The contract: when ``acsChargeType`` is absent from the body,
    # the key must NOT appear in ``validated_data``. If it does, the
    # default leaked.
    assert "acs_charge_type" not in serializer.validated_data, (
        "Regression: ``acs_charge_type`` should be absent from "
        "validated_data when the request body omits it. Got "
        f"{serializer.validated_data.get('acs_charge_type')!r}. "
        "This means the serializer is injecting a default that will "
        "leak past the carrier-level COD default and ACS will reject "
        "every PREPAID voucher. See orders 53/55/56."
    )
