"""Opting out of marketing mail: the hash, the rows and the signed link."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest
from django.core import signing

from emails import optout
from emails.models import EVERY_MARKETING_TYPE, EmailOptOut
from emails.registry import UnknownEmailType
from emails.types import Kind
from tests.utils.emails import probe

pytestmark = pytest.mark.django_db

ADDRESS = "Customer@Example.test"
MARKETING = "cart.checkout_abandoned"
OTHER_MARKETING = "product.restock_alert"
TRANSACTIONAL = "order.shipped"


@pytest.fixture(autouse=True)
def _store(bind_tenant_and_schema):
    bind_tenant_and_schema(SimpleNamespace(schema_name="acme"))


@pytest.fixture
def types(catalogue):
    return catalogue(
        probe(MARKETING, kind=Kind.MARKETING),
        probe(OTHER_MARKETING, kind=Kind.MARKETING),
        probe(TRANSACTIONAL),
    )


class TestAddressHash:
    def test_it_is_a_sha256_hex_digest(self):
        assert len(optout.address_hash(ADDRESS)) == 64

    def test_case_and_surrounding_space_do_not_matter(self):
        assert optout.address_hash("  customer@example.TEST ") == (
            optout.address_hash(ADDRESS)
        )

    def test_another_address_is_another_hash(self):
        assert optout.address_hash("other@example.test") != (
            optout.address_hash(ADDRESS)
        )

    def test_the_key_is_part_of_it(self, settings):
        before = optout.address_hash(ADDRESS)

        settings.EMAIL_HASH_KEY = "another key"

        assert optout.address_hash(ADDRESS) != before

    def test_it_does_not_contain_the_address(self):
        assert "example" not in optout.address_hash(ADDRESS)


class TestIsOptedOut:
    def test_a_row_for_the_type_opts_out_of_it_only(self, types):
        by_key = {t.key: t for t in types}
        optout.opt_out(ADDRESS, MARKETING, source=EmailOptOut.Source.LINK)

        assert optout.is_opted_out(ADDRESS, by_key[MARKETING]) is True
        assert optout.is_opted_out(ADDRESS, by_key[OTHER_MARKETING]) is False

    def test_the_star_row_opts_out_of_every_marketing_type(self, types):
        by_key = {t.key: t for t in types}
        optout.opt_out(
            ADDRESS, EVERY_MARKETING_TYPE, source=EmailOptOut.Source.ACCOUNT
        )

        assert optout.is_opted_out(ADDRESS, by_key[MARKETING]) is True
        assert optout.is_opted_out(ADDRESS, by_key[OTHER_MARKETING]) is True

    def test_another_address_is_not_opted_out(self, types):
        optout.opt_out(ADDRESS, MARKETING, source=EmailOptOut.Source.LINK)

        assert optout.is_opted_out("other@example.test", types[0]) is False

    def test_a_row_never_stops_a_transactional_type(self, types):
        EmailOptOut.objects.create(
            address_hash=optout.address_hash(ADDRESS),
            type_key=TRANSACTIONAL,
            source=EmailOptOut.Source.STAFF,
        )
        EmailOptOut.objects.create(
            address_hash=optout.address_hash(ADDRESS),
            type_key=EVERY_MARKETING_TYPE,
            source=EmailOptOut.Source.STAFF,
        )

        assert optout.is_opted_out(ADDRESS, types[2]) is False


class TestOptOut:
    def test_it_is_recorded_once(self, types):
        first = optout.opt_out(
            ADDRESS, MARKETING, source=EmailOptOut.Source.LINK
        )
        again = optout.opt_out(
            ADDRESS, MARKETING, source=EmailOptOut.Source.ONE_CLICK
        )

        assert first.pk == again.pk
        assert EmailOptOut.objects.count() == 1
        assert again.source == EmailOptOut.Source.LINK

    def test_the_table_holds_no_address(self, types):
        optout.opt_out(ADDRESS, MARKETING, source=EmailOptOut.Source.LINK)

        [row] = EmailOptOut.objects.values()

        assert "example" not in repr(row).lower()

    def test_a_type_that_takes_no_opt_out_is_refused(self, types):
        with pytest.raises(ValueError, match="no opt-out"):
            optout.opt_out(
                ADDRESS, TRANSACTIONAL, source=EmailOptOut.Source.LINK
            )

        assert not EmailOptOut.objects.exists()

    def test_an_unknown_type_is_refused(self, types):
        with pytest.raises(UnknownEmailType):
            optout.opt_out(ADDRESS, "no.such", source=EmailOptOut.Source.LINK)


class TestToken:
    def test_it_carries_the_hash_and_the_type(self, types):
        token = optout.make_token(ADDRESS, MARKETING)

        claims = optout.read_token(token)

        assert claims == optout.TokenClaims(
            address_hash=optout.address_hash(ADDRESS), type_key=MARKETING
        )

    def test_it_does_not_carry_the_address(self, types):
        payload = signing.loads(
            optout.make_token(ADDRESS, MARKETING), salt=optout.TOKEN_SALT
        )

        assert "example" not in repr(payload).lower()

    def test_following_it_opts_the_address_out(self, types):
        token = optout.make_token(ADDRESS, MARKETING)

        optout.opt_out_by_token(token, source=EmailOptOut.Source.ONE_CLICK)

        assert optout.is_opted_out(ADDRESS, types[0]) is True
        assert EmailOptOut.objects.get().source == EmailOptOut.Source.ONE_CLICK

    def test_following_it_twice_is_one_row(self, types):
        token = optout.make_token(ADDRESS, MARKETING)

        optout.opt_out_by_token(token, source=EmailOptOut.Source.LINK)
        optout.opt_out_by_token(token, source=EmailOptOut.Source.LINK)

        assert EmailOptOut.objects.count() == 1

    def test_the_star_token_covers_every_marketing_type(self, types):
        optout.opt_out_by_token(
            optout.make_token(ADDRESS, EVERY_MARKETING_TYPE),
            source=EmailOptOut.Source.LINK,
        )

        assert optout.is_opted_out(ADDRESS, types[1]) is True

    def test_a_forged_token_is_refused(self, types):
        token = optout.make_token(ADDRESS, MARKETING)

        with pytest.raises(optout.InvalidToken):
            optout.read_token(token[:-2] + "xx")

    def test_a_token_signed_for_another_purpose_is_refused(self, types):
        token = signing.dumps(
            {"schema": "acme", "h": "x", "t": MARKETING},
            salt="user.unsubscribe",
        )

        with pytest.raises(optout.InvalidToken):
            optout.read_token(token)

    def test_an_expired_token_is_refused(self, types):
        token = optout.make_token(ADDRESS, MARKETING)
        later = signing.time.time() + optout.TOKEN_MAX_AGE.total_seconds() + 60

        with (
            mock.patch("django.core.signing.time.time", return_value=later),
            pytest.raises(optout.InvalidToken),
        ):
            optout.read_token(token)

    def test_a_token_from_another_store_is_refused(
        self, types, bind_tenant_and_schema
    ):
        token = optout.make_token(ADDRESS, MARKETING)
        bind_tenant_and_schema(SimpleNamespace(schema_name="beta"))

        with pytest.raises(optout.InvalidToken, match="another store"):
            optout.read_token(token)

        assert not EmailOptOut.objects.exists()

    def test_a_token_for_a_type_that_takes_no_opt_out_is_refused(self, types):
        with pytest.raises(optout.InvalidToken, match="no opt-out"):
            optout.read_token(optout.make_token(ADDRESS, TRANSACTIONAL))

    def test_a_token_for_a_type_that_left_the_catalogue_is_refused(self, types):
        with pytest.raises(optout.InvalidToken):
            optout.read_token(optout.make_token(ADDRESS, "no.such"))
