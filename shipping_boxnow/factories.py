"""Factories for shipping_boxnow models.

Extends CustomDjangoModelFactory per the project pattern.
No django-parler translations (auto_translations=False) — BoxNow models
are not TranslatableModel subclasses.
"""

from __future__ import annotations

import factory
from django.utils import timezone

from devtools.factories import CustomDjangoModelFactory
from shipping_boxnow.enum.parcel_state import BoxNowParcelState
from shipping_boxnow.models.locker import BoxNowLocker
from shipping_boxnow.models.parcel_event import BoxNowParcelEvent
from shipping_boxnow.models.shipment import BoxNowShipment


class BoxNowLockerFactory(CustomDjangoModelFactory):
    """Factory for BoxNowLocker — local APM cache entries."""

    auto_translations = False

    external_id = factory.Sequence(lambda n: f"apm-{n:04d}")
    type = "apm"
    name = factory.Faker("company")
    title = factory.Faker("company")
    lat = factory.Faker(
        "pydecimal", left_digits=2, right_digits=7, positive=True
    )
    lng = factory.Faker(
        "pydecimal", left_digits=2, right_digits=7, positive=True
    )
    address_line_1 = factory.Faker("street_address")
    address_line_2 = ""
    postal_code = factory.Faker("postcode")
    country_code = "GR"
    note = ""
    is_active = True
    last_synced_at = factory.LazyFunction(timezone.now)

    class Meta:
        model = BoxNowLocker
        skip_postgeneration_save = True


class BoxNowShipmentFactory(CustomDjangoModelFactory):
    """Factory for BoxNowShipment — one per Order."""

    auto_translations = False

    order = factory.SubFactory("order.factories.order.OrderFactory")
    locker = None
    # null (not blank "") for unique fields so multiple pending shipments
    # can coexist before the BoxNow API assigns real IDs.
    delivery_request_id = None
    parcel_id = None
    locker_external_id = factory.Sequence(lambda n: f"apm-{n:04d}")
    parcel_state = BoxNowParcelState.PENDING_CREATION
    compartment_size = 1
    weight_grams = 0
    payment_mode = "prepaid"
    allow_return = True

    class Meta:
        model = BoxNowShipment
        skip_postgeneration_save = True

    class Params:
        with_parcel = factory.Trait(
            delivery_request_id=factory.Sequence(lambda n: f"dreq-{40000 + n}"),
            parcel_id=factory.Sequence(lambda n: f"{9200000000 + n}"),
            parcel_state=BoxNowParcelState.NEW,
        )


class BoxNowParcelEventFactory(CustomDjangoModelFactory):
    """Factory for BoxNowParcelEvent — webhook audit records."""

    auto_translations = False

    shipment = factory.SubFactory(BoxNowShipmentFactory)
    webhook_message_id = factory.Sequence(lambda n: f"msg-{n:06d}")
    event_type = BoxNowParcelState.NEW
    parcel_state = "new"
    event_time = factory.LazyFunction(timezone.now)
    display_name = factory.Faker("company")
    postal_code = factory.Faker("postcode")
    additional_information = ""
    raw_payload = factory.LazyAttribute(
        lambda o: {
            "specversion": "1.0",
            "type": "gr.boxnow.parcel_event_change",
            "id": o.webhook_message_id,
            "data": {"parcelId": "0000000000", "event": "new"},
        }
    )

    class Meta:
        model = BoxNowParcelEvent
        skip_postgeneration_save = True
