"""Who an email goes to, and in which language."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from emails.recipients import (
    Recipient,
    customer_of,
    platform_operators,
    store_operators,
)
from tenant.credentials import tenant_admin_recipients

pytestmark = pytest.mark.django_db


def bind_store(bind_tenant, **fields):
    bind_tenant(
        SimpleNamespace(
            schema_name="acme",
            default_locale=fields.pop("default_locale", "en"),
            **fields,
        )
    )


class TestCustomerOf:
    def test_an_order_names_its_email_and_its_own_language(self):
        order = SimpleNamespace(email="a@example.test", language_code="el")

        assert customer_of(order) == Recipient("a@example.test", "el")

    def test_an_order_without_a_language_uses_its_accounts(self):
        order = SimpleNamespace(
            email="a@example.test",
            language_code="",
            user=SimpleNamespace(language_code="el"),
        )

        assert customer_of(order).language == "el"

    def test_nobody_stating_a_language_gets_the_stores(self, bind_tenant):
        bind_store(bind_tenant, default_locale="el")
        guest = SimpleNamespace(email="a@example.test", language_code="")

        assert customer_of(guest).language == "el"


class TestStoreOperators:
    def test_contact_then_owner_in_the_stores_language(self, bind_tenant):
        bind_store(
            bind_tenant,
            default_locale="el",
            contact_email="contact@store.test",
            owner_email="owner@store.test",
        )

        assert store_operators() == [
            Recipient("contact@store.test", "el"),
            Recipient("owner@store.test", "el"),
        ]

    def test_a_shared_address_is_listed_once(self, bind_tenant):
        bind_store(
            bind_tenant,
            contact_email="same@store.test",
            owner_email="same@store.test",
        )

        assert store_operators() == [Recipient("same@store.test", "en")]

    def test_a_store_with_no_address_has_nobody_not_the_platform(
        self, bind_tenant, settings
    ):
        settings.ADMINS = ["platform@example.test"]
        bind_store(bind_tenant, contact_email="", owner_email="")

        assert store_operators() == []

    def test_the_platform_fallback_stays_with_its_old_callers(
        self, bind_tenant, settings
    ):
        """``tenant_admin_recipients`` keeps its ``ADMINS`` fallback until
        its callers move onto the pipeline; both read the same fields."""
        settings.ADMINS = ["platform@example.test"]
        bind_store(bind_tenant, contact_email="", owner_email="")
        assert tenant_admin_recipients() == ["platform@example.test"]

        bind_store(bind_tenant, contact_email="c@store.test", owner_email="")
        assert tenant_admin_recipients() == ["c@store.test"]


class TestPlatformOperators:
    def test_admins_in_the_platforms_language(self, settings):
        settings.ADMINS = ["ops@platform.test", "cto@platform.test"]
        settings.LANGUAGE_CODE = "el"

        assert platform_operators() == [
            Recipient("ops@platform.test", "el"),
            Recipient("cto@platform.test", "el"),
        ]

    def test_no_admins_is_nobody(self, settings):
        settings.ADMINS = []

        assert platform_operators() == []
