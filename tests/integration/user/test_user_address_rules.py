"""Saved addresses obey the same delivery-address rules as an order.

Checkout prefills from them, so an address book entry with a postcode
in the street-number field would reproduce prod order #316 on every
checkout that uses it.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from country.factories import CountryFactory
from region.factories import RegionFactory
from user.factories.account import UserAccountFactory
from user.factories.address import UserAddressFactory
from user.serializers.address import UserAddressWriteSerializer

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


@pytest.fixture
def greece():
    """Greece with its seeded Google format, created here rather than
    read from the migration seed: ``tests/integration/country/conftest.py``
    deletes the seeded row for the rest of its worker's session."""
    country = CountryFactory(alpha_2="GR", alpha_3="GRC")
    country.postal_code_pattern = r"\d{3} ?\d{2}"
    country.postal_code_example = "151 24"
    country.save()
    return country


def _payload(greece, **overrides):
    region = RegionFactory(country=greece)
    data = {
        "title": "Home",
        "first_name": "Jane",
        "last_name": "Doe",
        "street": "Εγνατίας",
        "street_number": "12",
        "city": "Θεσσαλονίκη",
        "zipcode": "54622",
        "phone": "+306900000001",
        "country": greece.pk,
        "region": region.pk,
    }
    data.update(overrides)
    return data


class TestWriteSerializer:
    def test_order_316_is_rejected_field_by_field(self, greece):
        serializer = UserAddressWriteSerializer(
            data=_payload(
                greece, street="1", street_number="70300", zipcode="ΑΒΓΔ"
            )
        )

        assert not serializer.is_valid()
        assert set(serializer.errors) == {"street", "street_number", "zipcode"}

    def test_postcode_is_stored_normalised(self, greece):
        serializer = UserAddressWriteSerializer(
            data=_payload(greece, zipcode="546 22")
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["zipcode"] == "546 22"

    def test_retitling_an_old_bad_address_passes(self, greece):
        address = UserAddressFactory(
            user=UserAccountFactory(num_addresses=0),
            country=greece,
            zipcode="ΑΒΓΔ",
        )

        serializer = UserAddressWriteSerializer(
            address, data={"title": "Parents"}, partial=True
        )

        assert serializer.is_valid(), serializer.errors


class TestModelClean:
    def test_changing_the_postcode_to_an_invalid_one_is_rejected(self, greece):
        address = UserAddressFactory(
            user=UserAccountFactory(num_addresses=0),
            country=greece,
            street="Εγνατίας",
            street_number="12",
            zipcode="54622",
            is_main=False,
        )
        address.zipcode = "ΑΒΓΔ"

        with pytest.raises(ValidationError) as excinfo:
            address.clean()

        assert "zipcode" in excinfo.value.message_dict
