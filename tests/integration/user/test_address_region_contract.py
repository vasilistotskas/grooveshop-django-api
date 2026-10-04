"""A saved address's ``region`` may be null, and the schema must say so.

``UserAddressWriteSerializer`` accepts a null region: a region is required
only when the country has any (``core.validators.address``). The read
serializers published ``region`` as a plain string, and the storefront
generates its Zod schemas from that document (``pnpm openapi-ts``), so a
single region-less address made ``GET /user/address`` fail to parse and
the shopper's whole address list — in checkout and in the account —
came back as an error.
"""

from __future__ import annotations

import pytest

from user.factories.address import UserAddressFactory
from user.serializers.address import (
    UserAddressDetailSerializer,
    UserAddressSerializer,
)

ADDRESS_COMPONENTS = ("UserAddress", "UserAddressDetail")


@pytest.mark.parametrize("component", ADDRESS_COMPONENTS)
def test_region_is_nullable_in_the_published_schema(openapi_schema, component):
    region = openapi_schema["components"]["schemas"][component]["properties"][
        "region"
    ]

    assert region.get("nullable") is True, region


@pytest.mark.django_db
@pytest.mark.parametrize(
    "serializer", [UserAddressSerializer, UserAddressDetailSerializer]
)
def test_an_address_without_a_region_serialises_null(serializer):
    address = UserAddressFactory(region=None)

    assert serializer(address).data["region"] is None
