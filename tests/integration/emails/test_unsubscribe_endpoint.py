"""``POST /api/v1/emails/unsubscribe/<token>``, the RFC 8058 target."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.core import signing
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from emails import optout
from emails.models import EVERY_MARKETING_TYPE, EmailOptOut
from emails.types import Kind
from tests.utils.emails import probe

pytestmark = pytest.mark.django_db

ADDRESS = "customer@example.test"
MARKETING = "cart.checkout_abandoned"
OTHER_MARKETING = "product.restock_alert"
TRANSACTIONAL = "order.shipped"
ONE_CLICK_BODY = {"List-Unsubscribe": "One-Click"}


@pytest.fixture(autouse=True)
def _store(bind_tenant_and_schema, catalogue):
    bind_tenant_and_schema(SimpleNamespace(schema_name="acme"))
    catalogue(
        probe(MARKETING, kind=Kind.MARKETING),
        probe(OTHER_MARKETING, kind=Kind.MARKETING),
        probe(TRANSACTIONAL),
    )


def _url(token: str) -> str:
    return reverse("email-unsubscribe", kwargs={"token": token})


def _post(token: str, **extra):
    return APIClient(HTTP_ACCEPT_LANGUAGE="en").post(_url(token), **extra)


def test_the_header_url_resolves_to_the_endpoint():
    token = optout.make_token(ADDRESS, MARKETING)

    assert _url(token) == f"{optout.UNSUBSCRIBE_PATH}/{token}"


def test_a_post_records_the_opt_out_with_an_empty_200():
    response = _post(optout.make_token(ADDRESS, MARKETING))

    assert response.status_code == status.HTTP_200_OK
    assert response.content == b""
    row = EmailOptOut.objects.get()
    assert row.address_hash == optout.address_hash(ADDRESS)
    assert row.type_key == MARKETING
    assert row.source == EmailOptOut.Source.ONE_CLICK


@pytest.mark.parametrize(
    "extra",
    [
        {"data": ONE_CLICK_BODY},
        {"data": ONE_CLICK_BODY, "format": "multipart"},
        {
            "data": "List-Unsubscribe=One-Click",
            "content_type": "application/x-www-form-urlencoded",
        },
    ],
    ids=["default", "multipart", "form-urlencoded"],
)
def test_it_takes_the_body_a_mailbox_provider_sends(extra):
    response = _post(optout.make_token(ADDRESS, MARKETING), **extra)

    assert response.status_code == status.HTTP_200_OK
    assert EmailOptOut.objects.count() == 1


def test_a_repeated_post_is_the_same_200_and_one_row():
    token = optout.make_token(ADDRESS, MARKETING)

    first = _post(token)
    second = _post(token)

    assert first.status_code == second.status_code == status.HTTP_200_OK
    assert EmailOptOut.objects.count() == 1


def test_a_token_for_every_marketing_type_opts_out_of_all():
    response = _post(optout.make_token(ADDRESS, EVERY_MARKETING_TYPE))

    assert response.status_code == status.HTTP_200_OK
    assert EmailOptOut.objects.get().type_key == EVERY_MARKETING_TYPE


def test_it_opts_out_of_the_named_type_only():
    _post(optout.make_token(ADDRESS, MARKETING))

    assert list(EmailOptOut.objects.values_list("type_key", flat=True)) == [
        MARKETING
    ]


def test_a_get_never_opts_anyone_out():
    token = optout.make_token(ADDRESS, MARKETING)

    response = APIClient().get(_url(token))

    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert not EmailOptOut.objects.exists()


def _forged(payload: dict) -> str:
    return signing.dumps(payload, salt=optout.TOKEN_SALT)


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(
            lambda: optout.make_token(ADDRESS, MARKETING)[:-2] + "xx",
            id="tampered",
        ),
        pytest.param(lambda: "not-a-token", id="garbage"),
        pytest.param(
            lambda: _forged(
                {"schema": "acme", "h": "x" * 64, "t": "no.such_type"}
            ),
            id="unknown-type",
        ),
        pytest.param(
            lambda: _forged(
                {"schema": "another", "h": "x" * 64, "t": MARKETING}
            ),
            id="other-store",
        ),
        pytest.param(
            lambda: _forged(
                {"schema": "acme", "h": "x" * 64, "t": TRANSACTIONAL}
            ),
            id="type-that-takes-no-opt-out",
        ),
        pytest.param(
            lambda: signing.dumps(
                {"schema": "acme", "h": "x" * 64, "t": MARKETING}, salt="other"
            ),
            id="other-salt",
        ),
    ],
)
def test_a_bad_token_is_a_400_that_says_nothing_about_why(token):
    response = _post(token())

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {
        "detail": "Invalid unsubscribe link",
        "code": "unsubscribe_token_invalid",
    }
    assert not EmailOptOut.objects.exists()
