"""Factories for shipping_acs models — used by integration tests."""

from __future__ import annotations

import factory
from django.utils import timezone

from devtools.factories import CustomDjangoModelFactory
from shipping.enum import ShippingKind
from shipping_acs.enum.charge_type import AcsChargeType
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.enum.shop_kind import AcsShopKind
from shipping_acs.models import (
    AcsPickupList,
    AcsShipment,
    AcsStation,
    AcsTrackingEvent,
)


class AcsStationFactory(CustomDjangoModelFactory):
    """Factory for AcsStation cache rows."""

    auto_translations = False

    external_id = factory.Sequence(lambda n: f"ACS-{n:04d}")
    branch_code = "1"
    shop_kind = AcsShopKind.SMARTPOINT_INBOUND
    name = factory.Faker("company")
    address_line_1 = factory.Faker("street_address")
    city = factory.Faker("city")
    postal_code = factory.Faker("postcode")
    country_code = "GR"
    phone = factory.Faker("phone_number")
    working_hours = "08:00-20:00"
    is_active = True
    last_synced_at = factory.LazyFunction(timezone.now)

    class Meta:
        model = AcsStation
        skip_postgeneration_save = True


class AcsPickupListFactory(CustomDjangoModelFactory):
    """Factory for AcsPickupList rows (daily manifest)."""

    auto_translations = False

    pickup_list_no = factory.Sequence(lambda n: f"PL-{n:08d}")
    issued_at = factory.LazyFunction(timezone.now)
    issued_by = None
    billing_code = "TEST_BILLING"
    voucher_count = 0

    class Meta:
        model = AcsPickupList
        skip_postgeneration_save = True


class AcsShipmentFactory(CustomDjangoModelFactory):
    """Factory for AcsShipment — one per Order."""

    auto_translations = False

    order = factory.SubFactory("order.factories.order.OrderFactory")
    voucher_no = None
    pickup_list = None
    station_destination = None
    station_destination_external_id = ""
    station_branch_destination = ""
    delivery_kind = ShippingKind.HOME_DELIVERY
    shipment_state = AcsShipmentState.PENDING_CREATION
    weight_grams = 500
    item_quantity = 1
    charge_type = AcsChargeType.PREPAID
    delivery_products = ""

    class Meta:
        model = AcsShipment
        skip_postgeneration_save = True

    class Params:
        with_voucher = factory.Trait(
            voucher_no=factory.Sequence(lambda n: f"{7000000000 + n}"),
            shipment_state=AcsShipmentState.NEW,
        )


class AcsTrackingEventFactory(CustomDjangoModelFactory):
    """Factory for AcsTrackingEvent rows."""

    auto_translations = False

    shipment = factory.SubFactory(AcsShipmentFactory, with_voucher=True)
    event_time = factory.LazyFunction(timezone.now)
    checkpoint_action = "Παράδοση Πελάτη"
    checkpoint_location = ""
    notes = ""
    event_fingerprint = factory.Sequence(lambda n: f"fp-{n:040d}")
    raw_payload = factory.LazyFunction(dict)

    class Meta:
        model = AcsTrackingEvent
        skip_postgeneration_save = True
