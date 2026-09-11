"""A locker with no image must serialize as ``null``, never ``""``.

``BoxNowLocker.image_url`` is declared nullable, so the published
OpenAPI contract is "a URL or null" and the storefront validates it as
``z.url().nullable()``. An empty string satisfies neither. Because the
order response embeds the locker
(``OrderDetailSerializer.boxnow_shipment`` → ``locker``), one blank
image failed response validation for the WHOLE payload: staging order
272 was created, charged to the customer's basket, emails sent — and
the shopper saw an error, because the storefront proxy 422'd on
``boxnowShipment.locker.imageUrl``. Every locker in BoxNow's catalogue
comes without an image, so this fired on every locker order.
"""

from __future__ import annotations

import pytest

from shipping_boxnow.factories import BoxNowLockerFactory
from shipping_boxnow.serializers.locker import BoxNowLockerSerializer
from shipping_boxnow.services import BoxNowService


def test_a_destination_with_no_image_maps_to_null():
    defaults = BoxNowService._locker_defaults_from_dest(
        {"locationType": "apm", "lat": 37.9, "lng": 23.7}
    )

    assert defaults["image_url"] is None


def test_an_empty_image_string_maps_to_null():
    defaults = BoxNowService._locker_defaults_from_dest(
        {"locationType": "apm", "imageUrl": "", "lat": 37.9, "lng": 23.7}
    )

    assert defaults["image_url"] is None


def test_a_real_image_is_kept():
    defaults = BoxNowService._locker_defaults_from_dest(
        {
            "locationType": "apm",
            "imageUrl": "https://boxnow.gr/apm/1.jpg",
            "lat": 37.9,
            "lng": 23.7,
        }
    )

    assert defaults["image_url"] == "https://boxnow.gr/apm/1.jpg"


@pytest.mark.django_db
def test_the_serialized_locker_never_carries_an_empty_image():
    """What the storefront actually validates. ``""`` here is the 422."""
    locker = BoxNowLockerFactory()
    locker.image_url = None
    locker.save(update_fields=["image_url"])

    payload = BoxNowLockerSerializer(locker).data

    assert payload["image_url"] is None, (
        "an empty string fails the nullable-URL contract and 422s the "
        "whole order response"
    )
