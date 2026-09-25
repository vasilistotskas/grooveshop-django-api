"""Every order write path applies ``core.validators.address``.

Prod order #316 (webside, ACS home delivery) was accepted with street
"1", street number "70300" and a non-numeric postcode; ACS then refused
the voucher. Checkout (the create serializer), an owner's PUT/PATCH
(``OrderWriteSerializer``) and the admin (``Order.clean``) must all
reject it — the last two only when the address is actually changed, so
an unrelated edit of an old order is not blocked.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from country.factories import CountryFactory
from order.factories.order import OrderFactory
from order.serializers.order import (
    OrderCreateFromCartSerializer,
    OrderWriteSerializer,
)
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory

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


def _create_payload(**overrides):
    pay_way = PayWayFactory(settlement=PaySettlement.ONLINE, active=True)
    data = {
        "pay_way_id": pay_way.id,
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "jane@example.com",
        "street": "Εγνατίας",
        "street_number": "12",
        "city": "Θεσσαλονίκη",
        "zipcode": "54622",
        "country_id": "GR",
        "phone": "+306900000001",
    }
    data.update(overrides)
    return data


class TestCheckoutSerializer:
    def test_order_316_is_rejected_field_by_field(self, greece):
        serializer = OrderCreateFromCartSerializer(
            data=_create_payload(
                street="1", street_number="70300", zipcode="ΑΒΓΔ"
            )
        )

        assert not serializer.is_valid()
        assert set(serializer.errors) == {"street", "street_number", "zipcode"}

    def test_postcode_is_stored_normalised(self, greece):
        serializer = OrderCreateFromCartSerializer(
            data=_create_payload(zipcode=" 546  22 ")
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["zipcode"] == "546 22"

    def test_an_unknown_country_is_a_field_error(self, greece):
        serializer = OrderCreateFromCartSerializer(
            data=_create_payload(country_id="QQ")
        )

        assert not serializer.is_valid()
        assert "country_id" in serializer.errors

    def test_street_number_is_required(self, greece):
        payload = _create_payload()
        del payload["street_number"]

        serializer = OrderCreateFromCartSerializer(data=payload)

        assert not serializer.is_valid()
        assert "street_number" in serializer.errors


class TestOwnerEdit:
    def test_changing_the_postcode_to_an_invalid_one_is_rejected(self, greece):
        order = OrderFactory(country=greece, zipcode="54622", num_order_items=0)

        serializer = OrderWriteSerializer(
            order, data={"zipcode": "ΑΒΓΔ"}, partial=True
        )

        assert not serializer.is_valid()
        assert "zipcode" in serializer.errors

    def test_an_edit_that_leaves_an_old_bad_address_alone_passes(self, greece):
        order = OrderFactory(country=greece, zipcode="ΑΒΓΔ", num_order_items=0)

        serializer = OrderWriteSerializer(
            order, data={"customer_notes": "ring twice"}, partial=True
        )

        assert serializer.is_valid(), serializer.errors


class TestAdminClean:
    def test_changing_the_address_to_an_invalid_one_is_rejected(self, greece):
        order = OrderFactory(
            country=greece,
            street="Εγνατίας",
            street_number="12",
            zipcode="54622",
            num_order_items=0,
        )
        order.zipcode = "ΑΒΓΔ"

        with pytest.raises(ValidationError) as excinfo:
            order.clean()

        assert "zipcode" in excinfo.value.message_dict

    def test_an_unrelated_save_of_an_old_bad_order_is_not_blocked(self, greece):
        order = OrderFactory(
            country=greece,
            street="1",
            street_number="70300",
            zipcode="ΑΒΓΔ",
            num_order_items=0,
        )
        order.customer_notes = "called the customer"

        order.clean()

    def test_fixing_the_address_normalises_the_postcode(self, greece):
        order = OrderFactory(
            country=greece,
            street="1",
            street_number="70300",
            zipcode="ΑΒΓΔ",
            num_order_items=0,
        )
        order.street = "Εγνατίας"
        order.street_number = "12"
        order.zipcode = " 703  00 "

        order.clean()

        assert order.zipcode == "703 00"
