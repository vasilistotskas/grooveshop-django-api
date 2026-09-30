"""Payload building and gating in ``meta_capi/services.py``.

Covers ``is_capi_enabled`` (returns a real bool), consent / event-id
extraction from ``Order.metadata`` (malformed shapes fail closed), user-data
normalisation, and the InitiateCheckout / Refund / Purchase custom data.
Pure builders — nothing is sent.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from djmoney.money import Money
from extra_settings.models import Setting

from meta_capi.services import (
    _consent_granted,
    _event_id_for,
    build_complete_registration_event,
    build_initiate_checkout_event,
    build_purchase_event,
    build_refund_event,
    is_capi_enabled,
    should_dispatch_for_order,
)
from order.factories.order import OrderFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def capi_tenant(bind_tenant):
    bind_tenant(
        SimpleNamespace(
            schema_name="test-capi-services",
            meta_pixel_id="1234567890",
            meta_capi_access_token="EAA-secret-token",
        )
    )
    Setting.objects.update_or_create(
        name="META_CAPI_ENABLED",
        defaults={"value": True, "value_type": "bool"},
    )


class TestGates:
    def test_is_capi_enabled_returns_true_not_the_token(self, capi_tenant):
        assert is_capi_enabled() is True

    def test_should_dispatch_requires_consent(self, capi_tenant):
        granted = SimpleNamespace(metadata={"meta": {"consent": {"ads": 1}}})
        denied = SimpleNamespace(metadata={"meta": {"consent": {}}})

        assert should_dispatch_for_order(granted) is True
        assert should_dispatch_for_order(denied) is False

    @pytest.mark.parametrize(
        "metadata",
        [
            None,
            {},
            {"meta": "granted"},
            {"meta": {"consent": "yes"}},
            {"meta": {"consent": {"ads": False}}},
        ],
        ids=["none", "empty", "meta-str", "consent-str", "ads-false"],
    )
    def test_malformed_or_missing_consent_is_not_consent(self, metadata):
        assert _consent_granted(SimpleNamespace(metadata=metadata)) is False


class TestEventIdExtraction:
    def test_known_key_is_returned_as_string(self):
        order = SimpleNamespace(
            metadata={"meta": {"event_ids": {"purchase": 12345}}}
        )

        assert _event_id_for(order, "purchase") == "12345"

    def test_key_outside_the_allowlist_is_ignored(self):
        order = SimpleNamespace(
            metadata={"meta": {"event_ids": {"refund": "evt-x"}}}
        )

        assert _event_id_for(order, "refund") is None

    def test_non_dict_event_ids_are_ignored(self):
        order = SimpleNamespace(metadata={"meta": {"event_ids": ["evt"]}})

        assert _event_id_for(order, "purchase") is None


class TestUserData:
    def test_order_contact_fields_are_normalised(self):
        order = OrderFactory(
            email="  Shopper@Example.COM ",
            first_name=" Maria ",
            last_name="PAPADOPOULOU",
            city=" Athens ",
            zipcode=" 11521 ",
            metadata={"meta": {"fbp": "fb.1.1", "client_user_agent": "UA"}},
            num_order_items=0,
        )

        event, _ = build_purchase_event(order)

        user_data = event.user_data
        assert user_data.emails == ["shopper@example.com"]
        assert user_data.first_names == ["maria"]
        assert user_data.last_names == ["papadopoulou"]
        assert user_data.cities == ["athens"]
        assert user_data.zip_codes == ["11521"]
        assert user_data.fbp == "fb.1.1"
        assert user_data.client_user_agent == "UA"
        assert user_data.external_ids == [str(order.user_id)]

    def test_a_valid_phone_is_sent_in_e164_without_its_extension(self):
        order = OrderFactory(phone="+302101234567 ext. 12", num_order_items=0)

        event, _ = build_purchase_event(order)

        assert event.user_data.phones == ["+302101234567"]

    def test_an_invalid_stored_phone_is_left_out(self):
        """An invalid number is stored as its raw input, without a
        country code: its hash could never match, and the SDK refuses
        to normalise it, which would lose the whole event."""
        order = OrderFactory(num_order_items=0)
        order.phone = "2101"
        order.save(update_fields=["phone"])
        order.refresh_from_db()

        event, _ = build_purchase_event(order)

        assert event.user_data.phones is None
        assert event.normalize()["user_data"]["em"]

    def test_guest_order_has_no_external_id(self):
        order = OrderFactory(user=None, num_order_items=0)

        event, _ = build_purchase_event(order)

        assert event.user_data.external_ids is None


class TestCustomData:
    def test_initiate_checkout_value_is_the_expected_checkout_total(self):
        order = OrderFactory(num_order_items=2)
        order.shipping_price = Money(Decimal("3.00"), "EUR")
        order.payment_method_fee = Money(Decimal("1.50"), "EUR")
        order.discount_amount = Money(Decimal("2.00"), "EUR")
        order.loyalty_discount = Money(Decimal("0.50"), "EUR")
        order.paid_amount = Money(Decimal("0.00"), "EUR")
        items_total = order.total_price_items.amount

        event, event_id = build_initiate_checkout_event(order)

        custom = event.custom_data
        assert custom.value == float(
            items_total
            + Decimal("3.00")
            + Decimal("1.50")
            - Decimal("2.00")
            - Decimal("0.50")
        )
        assert custom.currency == "EUR"
        assert custom.num_items == sum(i.quantity for i in order.items.all())
        assert sorted(custom.content_ids) == sorted(
            str(i.product_id) for i in order.items.all()
        )
        # No browser-minted id: a fresh random one is used.
        assert len(event_id) == 32

    def test_initiate_checkout_value_never_goes_negative(self):
        order = OrderFactory(num_order_items=1)
        order.discount_amount = Money(Decimal("100000.00"), "EUR")

        event, _ = build_initiate_checkout_event(order)

        assert event.custom_data.value == 0.0

    def test_refund_explicit_amount_wins_over_paid_amount(self):
        order = OrderFactory(num_order_items=0)
        order.paid_amount = Money(Decimal("40.00"), "EUR")

        explicit, id_a = build_refund_event(order, Decimal("15.25"))
        fallback, id_b = build_refund_event(order, None)

        assert explicit.custom_data.value == 15.25
        assert fallback.custom_data.value == 40.0
        assert explicit.custom_data.currency == "EUR"
        # Refunds carry a fresh id each time (no browser leg to dedup).
        assert id_a != id_b

    def test_purchase_reports_paid_amount_and_order_id(self):
        order = OrderFactory(num_order_items=1)
        order.paid_amount = Money(Decimal("27.40"), "EUR")

        event, _ = build_purchase_event(order)

        assert event.custom_data.value == 27.4
        assert event.custom_data.currency == "EUR"
        assert event.custom_data.order_id == str(order.id)


class TestCompleteRegistration:
    def test_generates_an_event_id_when_none_supplied(self):
        user = UserAccountFactory(email="New@Example.com")

        event, event_id = build_complete_registration_event(user)

        assert event.event_id == event_id
        assert len(event_id) == 32
        assert event.user_data.emails == ["new@example.com"]
        assert event.event_source_url is None
        assert event.custom_data is None
