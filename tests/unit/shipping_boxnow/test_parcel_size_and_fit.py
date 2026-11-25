"""The compartment size the quote names for a BOX NOW option, and the
exact-membership rule a locker must meet for it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from extra_settings.models import Setting
from measurement.measures import Distance

from shipping.enum import ShippingKind
from shipping.interfaces import PickupPointSizeUnknown, PickupPointTooSmall
from shipping_boxnow.carrier import BoxNowCarrier
from shipping_boxnow.factories import BoxNowLockerFactory

pytestmark = pytest.mark.django_db

LIVE = pytest.mark.usefixtures("boxnow_live_network")


def _product(height):
    return SimpleNamespace(
        length=Distance(cm=30),
        width=Distance(cm=20),
        height=Distance(cm=height),
    )


@LIVE
def test_a_measured_cart_names_the_size_it_packs_into():
    carrier = BoxNowCarrier()

    assert (
        carrier.parcel_size(
            kind=ShippingKind.PICKUP_POINT, items=[(_product(10), 1)]
        )
        == 2
    )


@LIVE
def test_a_cart_without_dimensions_follows_the_store_default():
    Setting.objects.update_or_create(
        name="BOXNOW_DEFAULT_COMPARTMENT_SIZE",
        defaults={"value_type": "int", "value_int": 3},
    )

    size = BoxNowCarrier().parcel_size(
        kind=ShippingKind.PICKUP_POINT,
        items=[(SimpleNamespace(length=None, width=None, height=None), 1)],
    )

    assert size == 3


@LIVE
def test_a_cart_that_fits_no_compartment_names_none():
    assert (
        BoxNowCarrier().parcel_size(
            kind=ShippingKind.PICKUP_POINT, items=[(_product(80), 1)]
        )
        is None
    )


@LIVE
def test_a_home_delivery_names_no_size():
    assert (
        BoxNowCarrier().parcel_size(
            kind=ShippingKind.HOME_DELIVERY, items=[(_product(5), 1)]
        )
        is None
    )


@LIVE
def test_a_locker_that_lists_the_size_fits_and_one_that_does_not_is_refused():
    carrier = BoxNowCarrier()
    medium_cart = [(_product(10), 1)]
    BoxNowLockerFactory(external_id="fit", compartment_sizes=[2, 3])
    BoxNowLockerFactory(external_id="skip", compartment_sizes=[1, 3])
    BoxNowLockerFactory(external_id="blank", compartment_sizes=[])

    carrier.assert_pickup_point_fits(
        {"boxnow_locker_id": "fit"}, items=medium_cart
    )
    with pytest.raises(PickupPointTooSmall):
        carrier.assert_pickup_point_fits(
            {"boxnow_locker_id": "skip"}, items=medium_cart
        )
    with pytest.raises(PickupPointSizeUnknown):
        carrier.assert_pickup_point_fits(
            {"boxnow_locker_id": "blank"}, items=medium_cart
        )
    # A locker the catalogue does not hold is the point resolution's refusal.
    carrier.assert_pickup_point_fits(
        {"boxnow_locker_id": "missing"}, items=medium_cart
    )


def test_a_stage_store_names_no_size_and_checks_no_locker(
    boxnow_configured_tenant,
):
    carrier = BoxNowCarrier()
    BoxNowLockerFactory(external_id="stage", compartment_sizes=[])

    assert (
        carrier.parcel_size(
            kind=ShippingKind.PICKUP_POINT, items=[(_product(10), 1)]
        )
        is None
    )
    carrier.assert_pickup_point_fits(
        {"boxnow_locker_id": "stage"}, items=[(_product(10), 1)]
    )
