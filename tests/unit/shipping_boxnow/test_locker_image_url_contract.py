"""A locker with no image must serialize as ``null``, never ``""``.

The published contract for ``imageUrl`` is "a URL or null", and the
storefront validates it as exactly that (``z.url().nullable()``). An
empty string is neither. Because the order response embeds the locker
(``OrderDetailSerializer.boxnow_shipment`` → ``locker``), one blank
image failed response validation for the WHOLE payload: staging order
272 was created, stock decremented and emails sent — and the shopper
saw an error, because the storefront proxy 422'd on
``boxnowShipment.locker.imageUrl``. Every locker in BoxNow's catalogue
comes without an image, so this fired on every locker order.

The fix is at the serializer, NOT the column: nullable string columns
are mid-migration to NOT NULL and phase one is that nothing keeps
minting NULLs (``tests/unit/core/test_nullable_string_fields.py``), so
the column keeps storing "" and the API maps that blank to null.
"""

from __future__ import annotations

import pytest

from shipping_boxnow.factories import BoxNowLockerFactory
from shipping_boxnow.serializers.locker import BoxNowLockerSerializer

pytestmark = pytest.mark.django_db


def _serialized(image_url):
    locker = BoxNowLockerFactory()
    locker.image_url = image_url
    locker.save(update_fields=["image_url"])
    return BoxNowLockerSerializer(locker).data["image_url"]


def test_a_blank_image_serializes_as_null():
    assert _serialized("") is None


def test_a_null_column_still_serializes_as_null():
    # Rows written before the default landed still hold NULL.
    assert _serialized(None) is None


def test_a_real_image_is_passed_through():
    assert _serialized("https://boxnow.gr/apm/1.jpg") == (
        "https://boxnow.gr/apm/1.jpg"
    )


def test_the_column_itself_keeps_the_empty_string_convention():
    """The fix must not reintroduce NULL-minting: a locker saved with no
    image gets "", because nullable string columns are mid-migration to
    NOT NULL."""
    field = BoxNowLockerFactory._meta.model._meta.get_field("image_url")

    assert field.get_default() == ""
