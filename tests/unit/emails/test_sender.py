"""The sender and the operator addresses of the email pipeline.

``emails.sender.from_address`` is the ``From`` of the active schema,
``store_reply_to`` its ``Reply-To``, and
``emails.recipients.store_operator_addresses`` the store's own inbox list.
"""

from __future__ import annotations

from django.db import connection

from emails.recipients import store_operator_addresses
from emails.sender import from_address, store_reply_to
from tests.utils.staff import store_tenant


class TestFromAddress:
    def test_an_unverified_merchant_address_is_never_used_as_from(
        self, bind_tenant, db, settings
    ):
        """DMARC safety: the relay can only sign for domains it has a
        DKIM key for. An address the platform has not verified would
        fail the merchant domain's alignment, so the tenant brand rides
        the DISPLAY NAME on the platform-authenticated address instead.
        Setting from_email alone must change nothing."""
        tenant = store_tenant("email_from_1")
        tenant.from_email = "shop@brand.com"
        tenant.store_name = "Brand Shop"
        tenant.save()
        bind_tenant(tenant)
        settings.DEFAULT_FROM_EMAIL = "noreply@platform.com"
        assert from_address() == "Brand Shop <noreply@platform.com>"
        assert "shop@brand.com" not in from_address()

    def test_a_verified_merchant_address_becomes_the_from(
        self, bind_tenant, db, settings
    ):
        """The white-label case. Once the platform has authenticated the
        store's domain on the relay, the relay holds a DKIM key for it,
        so the merchant's own address is both usable and preferable —
        customers see the shop's domain, not the platform's."""
        tenant = store_tenant("email_from_verified")
        tenant.from_email = "orders@brand.com"
        tenant.from_email_verified = True
        tenant.store_name = "Brand Shop"
        tenant.save()
        bind_tenant(tenant)
        settings.DEFAULT_FROM_EMAIL = "noreply@platform.com"
        assert from_address() == "Brand Shop <orders@brand.com>"

    def test_the_flag_alone_does_nothing_without_an_address(
        self, bind_tenant, db, settings
    ):
        """Verified but blank must not produce "Store <>", which is not
        a valid RFC 5322 address."""
        tenant = store_tenant("email_from_flag_only")
        tenant.from_email = ""
        tenant.from_email_verified = True
        tenant.store_name = "Flag Only"
        tenant.save()
        bind_tenant(tenant)
        settings.DEFAULT_FROM_EMAIL = "noreply@platform.com"
        assert from_address() == "Flag Only <noreply@platform.com>"

    def test_falls_back_to_settings(self, bind_tenant, db, settings):
        tenant = store_tenant("email_from_2")
        tenant.from_email = ""
        tenant.store_name = "Fallback Store"
        tenant.save()
        bind_tenant(tenant)
        settings.DEFAULT_FROM_EMAIL = "noreply@platform.com"
        assert from_address() == "Fallback Store <noreply@platform.com>"

    def test_no_tenant_uses_settings(self, monkeypatch, settings):
        monkeypatch.setattr(connection, "tenant", None, raising=False)
        settings.DEFAULT_FROM_EMAIL = "platform@example.com"
        assert from_address() == "platform@example.com"

    def test_webside_safety_empty_tenant_field(self, bind_tenant, db, settings):
        """webside.gr: empty from_email → platform address with the
        store display name (envelope/domain unchanged)."""
        tenant = store_tenant("ws_from_email")
        tenant.store_name = "Webside"
        tenant.save()
        bind_tenant(tenant)
        settings.DEFAULT_FROM_EMAIL = "noreply@webside.gr"
        assert from_address() == "Webside <noreply@webside.gr>"


class TestStoreReplyTo:
    """``store_reply_to()`` — the ``Reply-To`` list for outbound mail."""

    def test_store_with_contact_address(self, bind_tenant, db):
        tenant = store_tenant("reply_to_1")
        tenant.contact_email = "contact@shop.com"
        tenant.save()
        bind_tenant(tenant)
        assert store_reply_to() == ["contact@shop.com"]

    def test_store_without_contact_address_emits_no_header(
        self, bind_tenant, db, settings
    ):
        # ``EmailMessage`` joins the list verbatim, so ``[""]`` would put
        # a literal empty ``Reply-To:`` header on the wire; ``[]`` emits
        # none at all.
        from django.core.mail import EmailMultiAlternatives
        from extra_settings.models import Setting

        tenant = store_tenant("reply_to_2")
        tenant.contact_email = ""
        tenant.save()
        bind_tenant(tenant)
        settings.INFO_EMAIL = "info@platform.com"
        Setting.objects.filter(name="CONTACT_EMAIL").delete()

        assert store_reply_to() == []
        message = EmailMultiAlternatives(
            "s",
            "b",
            "from@example.com",
            ["to@example.com"],
            reply_to=store_reply_to(),
        ).message()
        assert "Reply-To" not in message


class TestStoreOperatorAddresses:
    def test_contact_then_owner_without_duplicates(self, bind_tenant, db):
        bind_tenant(
            store_tenant(
                "operators_1",
                contact_email="contact@shop.com",
                owner_email="owner@shop.com",
            )
        )

        assert store_operator_addresses() == [
            "contact@shop.com",
            "owner@shop.com",
        ]

    def test_the_same_address_twice_is_one(self, bind_tenant, db):
        bind_tenant(
            store_tenant(
                "operators_2",
                contact_email="same@shop.com",
                owner_email="same@shop.com",
            )
        )

        assert store_operator_addresses() == ["same@shop.com"]

    def test_a_store_with_neither_has_nobody(self, bind_tenant, db, settings):
        settings.ADMINS = ["platform@example.com"]
        bind_tenant(
            store_tenant("operators_3", contact_email="", owner_email="")
        )

        assert store_operator_addresses() == []

    def test_no_store_has_nobody(self, bind_tenant):
        bind_tenant(None)

        assert store_operator_addresses() == []
