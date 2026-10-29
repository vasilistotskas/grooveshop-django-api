"""The rules a single email type must satisfy to exist."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from emails.types import Audience, Kind, Owner
from tests.utils.emails import probe


class TestKey:
    @pytest.mark.parametrize(
        "key", ["order.shipped", "team.new_order", "newsletter", "a1.b2.c3"]
    )
    def test_accepts_lowercase_words_joined_by_dots(self, key):
        assert probe(key).key == key

    @pytest.mark.parametrize(
        "key",
        [
            "Order.shipped",
            "order shipped",
            "order-shipped",
            "order..shipped",
            ".order",
            "order.",
            "παραγγελία",
            "",
            "a" * 65,
        ],
    )
    def test_refuses_anything_else(self, key):
        with pytest.raises(ImproperlyConfigured, match="key"):
            probe(key)

    def test_the_longest_key_the_tables_hold_is_accepted(self):
        assert probe("a" * 64).key == "a" * 64


class TestInvariants:
    def test_marketing_mail_cannot_be_required(self):
        with pytest.raises(ImproperlyConfigured, match="marketing"):
            probe(kind=Kind.MARKETING, required=True)

    def test_a_platform_type_cannot_be_sent_to_customers(self):
        with pytest.raises(ImproperlyConfigured, match="platform"):
            probe(owner=Owner.PLATFORM, audience=Audience.CUSTOMER)

    @pytest.mark.parametrize(
        "audience", [Audience.STORE_STAFF, Audience.PLATFORM_OPERATOR]
    )
    def test_a_platform_type_may_write_to_staff_and_operators(self, audience):
        assert probe(owner=Owner.PLATFORM, audience=audience)

    def test_every_broken_rule_is_named_at_once(self):
        with pytest.raises(ImproperlyConfigured) as raised:
            probe("Bad Key", kind=Kind.MARKETING, required=True)

        assert "key" in str(raised.value)
        assert "marketing" in str(raised.value)


class TestSubject:
    @pytest.mark.parametrize(
        "subject",
        [
            "{order.user.password}",
            "{order.__class__}",
            "{items[0]}",
            "{0}",
            "{}",
            "{x!r}",
            "{x:>10}",
            "{unclosed",
        ],
    )
    def test_only_plain_named_fields_are_accepted(self, subject):
        with pytest.raises(ImproperlyConfigured, match="subject"):
            probe(subject=subject)

    def test_plain_fields_and_escaped_braces_are_accepted(self):
        assert probe(subject="Order #{order_id} {{raw}}")


class TestDerivedValues:
    def test_the_list_id_is_the_key_with_dashes(self):
        assert probe("order.payment_failed").list_id == "order-payment_failed"

    def test_a_stated_list_id_is_kept(self):
        assert probe(
            "order.received", list_id="order_confirmation"
        ).list_id == ("order_confirmation")

    def test_only_a_required_type_is_not_switchable(self):
        assert probe(required=False).switchable is True
        assert probe(required=True).switchable is False

    def test_only_marketing_takes_an_opt_out(self):
        assert probe(kind=Kind.MARKETING).opt_out_applies is True
        assert probe(kind=Kind.TRANSACTIONAL).opt_out_applies is False
