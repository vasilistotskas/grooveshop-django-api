"""Unit tests for the GDPR erasure service (``anonymise_and_delete_user``).

Focus: right-to-erasure must leave no recipient PII behind, including the
carrier shipment ``metadata['last_error']`` envelope that persists a failed
voucher/parcel request payload (name, address, phone) outside the order row.
"""

from __future__ import annotations

import pytest

from shipping_acs.factories import AcsShipmentFactory
from shipping_boxnow.factories import BoxNowShipmentFactory
from user.factories.account import UserAccountFactory
from user.services.gdpr import (
    anonymise_and_delete_user,
    compile_user_data,
)


@pytest.mark.django_db
class TestAnonymiseAndDeleteUserCarrierPII:
    def test_last_error_pii_scrubbed_from_carrier_shipments(self):
        """The ``last_error`` envelope (recipient PII) is removed from both
        ACS and BoxNow shipments linked to the user's orders, while the
        non-personal operational metadata is retained."""
        user = UserAccountFactory()

        acs = AcsShipmentFactory(
            order__user=user,
            with_voucher=True,
            metadata={
                "billing_code": "TEST_BILLING",
                "last_error": {
                    "occurred_at": "2026-07-14T00:00:00+00:00",
                    "error": "Error fill data error",
                    "request_params": {
                        "Recipient_Name": "Jane Doe",
                        "Recipient_Address": "10 Main St",
                        "Recipient_Phone": "6900000000",
                    },
                },
            },
        )
        boxnow = BoxNowShipmentFactory(
            order__user=user,
            with_parcel=True,
            metadata={
                "create_response": {"ok": True},
                "last_error": {
                    "error": "boxnow rejected",
                    "request_params": {
                        "destinationContactName": "Jane Doe",
                        "destinationContactPhone": "6900000000",
                    },
                },
            },
        )

        counts = anonymise_and_delete_user(user)

        assert counts["carrier_pii_scrubbed"] == 2

        acs.refresh_from_db()
        boxnow.refresh_from_db()

        assert "last_error" not in acs.metadata
        assert "last_error" not in boxnow.metadata
        # Non-personal operational metadata is retained.
        assert acs.metadata["billing_code"] == "TEST_BILLING"
        assert boxnow.metadata["create_response"] == {"ok": True}

    def test_scrub_skips_shipments_without_last_error(self):
        """Shipments with no ``last_error`` are left untouched and not
        counted as scrubbed."""
        user = UserAccountFactory()
        acs = AcsShipmentFactory(
            order__user=user,
            with_voucher=True,
            metadata={"billing_code": "TEST_BILLING"},
        )

        counts = anonymise_and_delete_user(user)

        assert counts["carrier_pii_scrubbed"] == 0
        acs.refresh_from_db()
        assert acs.metadata == {"billing_code": "TEST_BILLING"}


@pytest.mark.django_db
class TestCompileUserDataCompleteness:
    def test_export_includes_search_history_and_cart(self):
        """Right-of-access must include the user's search history and their
        current cart — both are personal data linked to the subject."""
        from cart.factories.cart import CartFactory
        from cart.factories.item import CartItemFactory
        from search.models import SearchQuery

        user = UserAccountFactory()

        SearchQuery.objects.create(
            user=user,
            query="running shoes",
            language_code="en",
            content_type="product",
            results_count=5,
            estimated_total_hits=42,
            ip_address="203.0.113.7",
            user_agent="pytest-agent",
        )
        cart = CartFactory(user=user)
        item = CartItemFactory(cart=cart, quantity=3)

        data = compile_user_data(user)

        assert "search_history" in data
        assert len(data["search_history"]) == 1
        entry = data["search_history"][0]
        assert entry["query"] == "running shoes"
        assert entry["ip_address"] == "203.0.113.7"

        assert data["cart"] is not None
        assert data["cart"]["id"] == cart.id
        assert len(data["cart"]["items"]) == 1
        assert data["cart"]["items"][0]["product_id"] == item.product_id
        assert data["cart"]["items"][0]["quantity"] == 3

    def test_export_cart_is_none_without_cart(self):
        """No cart → the ``cart`` key is present and explicitly null."""
        user = UserAccountFactory()

        data = compile_user_data(user)

        assert data["cart"] is None
        assert data["search_history"] == []


@pytest.mark.django_db
class TestCompileUserDataAlertsAndContacts:
    """Product alerts and contact messages are personal data too."""

    @staticmethod
    def _verified(user, email):
        from allauth.account.models import EmailAddress

        EmailAddress.objects.filter(user=user).delete()
        EmailAddress.objects.create(
            user=user, email=email, verified=True, primary=True
        )

    def test_export_includes_the_users_product_alerts(self):
        from product.factories.product import ProductFactory
        from product.models.alert import ProductAlert, ProductAlertKind

        user = UserAccountFactory()
        other = UserAccountFactory()
        product = ProductFactory(num_images=0, num_reviews=0)
        ProductAlert.objects.create(
            user=user, product=product, kind=ProductAlertKind.RESTOCK
        )
        ProductAlert.objects.create(
            user=other, product=product, kind=ProductAlertKind.RESTOCK
        )

        alerts = compile_user_data(user)["product_alerts"]

        assert [a["product_id"] for a in alerts] == [product.pk]
        assert alerts[0]["kind"] == ProductAlertKind.RESTOCK

    def test_export_includes_guest_alerts_on_a_verified_address(self):
        from product.factories.product import ProductFactory
        from product.models.alert import ProductAlert, ProductAlertKind

        user = UserAccountFactory()
        self._verified(user, "me@example.com")
        product = ProductFactory(num_images=0, num_reviews=0)
        ProductAlert.objects.create(
            email="ME@example.com",
            product=product,
            kind=ProductAlertKind.RESTOCK,
        )
        ProductAlert.objects.create(
            email="stranger@example.com",
            product=product,
            kind=ProductAlertKind.RESTOCK,
        )

        alerts = compile_user_data(user)["product_alerts"]

        assert [a["email"] for a in alerts] == ["ME@example.com"]

    def test_export_includes_contact_messages_from_a_verified_address(self):
        from contact.models import Contact

        user = UserAccountFactory()
        self._verified(user, "me@example.com")
        Contact.objects.create(
            name="Me", email="Me@Example.com", message="Where is my order?"
        )
        Contact.objects.create(
            name="Stranger", email="x@example.com", message="Not mine"
        )

        messages = compile_user_data(user)["contact_messages"]

        assert [m["message"] for m in messages] == ["Where is my order?"]

    def test_unverified_address_exports_no_contact_messages(self):
        from allauth.account.models import EmailAddress

        from contact.models import Contact

        user = UserAccountFactory()
        EmailAddress.objects.filter(user=user).delete()
        EmailAddress.objects.create(
            user=user, email="me@example.com", verified=False, primary=True
        )
        Contact.objects.create(
            name="Someone", email="me@example.com", message="Not proven"
        )

        data = compile_user_data(user)

        assert data["contact_messages"] == []
