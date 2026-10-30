"""``GET`` and ``PATCH /api/v1/user/email-preferences``: a customer reads,
declines and lifts their own marketing opt-outs."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from emails import optout
from emails.gates import FeatureGate
from emails.models import EVERY_MARKETING_TYPE, EmailOptOut, EmailSwitch
from emails.registry import registry
from tests.utils.emails import open_email_gates  # noqa: F401
from user.factories.account import UserAccountFactory
from user.services.subscription import opt_out_of_marketing

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("open_email_gates"),
]

RESTOCK = "product.restock_alert"
PRICE_DROP = "product.price_drop_alert"


def _marketing_keys() -> set[str]:
    return {t.key for t in registry.all_types() if t.opt_out_applies}


@pytest.fixture
def user():
    return UserAccountFactory(num_addresses=0)


@pytest.fixture
def client(user):
    api = APIClient(HTTP_ACCEPT_LANGUAGE="en")
    api.force_authenticate(user)
    return api


def _url() -> str:
    return reverse("user-email-preferences")


def _state(response) -> dict[str, bool]:
    return {row["key"]: row["enabled"] for row in response.data["types"]}


def _keys(address: str) -> set[str]:
    return set(
        EmailOptOut.objects.filter(
            address_hash=optout.address_hash(address)
        ).values_list("type_key", flat=True)
    )


def _patch(client, **choices: bool):
    return client.patch(
        _url(),
        {"types": [{"key": k, "enabled": v} for k, v in choices.items()]},
        format="json",
    )


def test_the_endpoint_needs_a_signed_in_customer():
    response = APIClient().get(_url())

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_lists_every_offered_marketing_type_on(client):
    response = client.get(_url())

    assert response.status_code == status.HTTP_200_OK
    assert set(_state(response)) == _marketing_keys()
    assert all(_state(response).values())
    assert all(row["title"] for row in response.data["types"])


def test_get_reflects_a_per_type_row(client, user):
    optout.opt_out(user.email, RESTOCK, source=EmailOptOut.Source.LINK)

    state = _state(client.get(_url()))

    assert state[RESTOCK] is False
    assert state[PRICE_DROP] is True


def test_get_reflects_the_every_type_row(client, user):
    optout.opt_out(
        user.email, EVERY_MARKETING_TYPE, source=EmailOptOut.Source.ONE_CLICK
    )

    assert not any(_state(client.get(_url())).values())


def test_get_hides_a_type_whose_gate_is_closed(client):
    with patch.object(FeatureGate, "is_open", return_value=False):
        state = _state(client.get(_url()))

    assert RESTOCK not in state
    assert PRICE_DROP not in state


def test_get_hides_a_type_the_store_switched_off(client):
    EmailSwitch.objects.create(type_key=RESTOCK)

    state = _state(client.get(_url()))

    assert RESTOCK not in state
    assert PRICE_DROP in state


def test_declining_records_a_per_type_row(client, user):
    response = _patch(client, **{RESTOCK: False})

    assert response.status_code == status.HTTP_200_OK
    assert _state(response)[RESTOCK] is False
    assert _keys(user.email) == {RESTOCK}
    assert EmailOptOut.objects.get().source == EmailOptOut.Source.ACCOUNT


def test_enabling_under_the_every_type_row_keeps_the_others_off(client, user):
    opt_out_of_marketing(
        user.email, account_pk=user.pk, source=EmailOptOut.Source.ONE_CLICK
    )

    response = _patch(client, **{RESTOCK: True})

    state = _state(response)
    assert state[RESTOCK] is True
    assert not any(v for k, v in state.items() if k != RESTOCK)
    assert _keys(user.email) == _marketing_keys() - {RESTOCK}
    assert (
        EmailOptOut.objects.filter(source=EmailOptOut.Source.ONE_CLICK).count()
        == len(_marketing_keys()) - 1
    ), "the split rows keep the source of the row they replace"
    user.refresh_from_db()
    assert user.marketing_opted_out_at is not None, "other rows remain"


def test_enabling_every_type_in_one_request_clears_the_flag(client, user):
    opt_out_of_marketing(
        user.email, account_pk=user.pk, source=EmailOptOut.Source.LINK
    )

    response = _patch(client, **dict.fromkeys(_marketing_keys(), True))

    assert all(_state(response).values())
    assert _keys(user.email) == set()
    user.refresh_from_db()
    assert user.marketing_opted_out_at is None


def test_enabling_the_last_row_clears_the_flag(client, user):
    opt_out_of_marketing(
        user.email, account_pk=user.pk, source=EmailOptOut.Source.LINK
    )
    _patch(client, **dict.fromkeys(_marketing_keys() - {RESTOCK}, True))
    user.refresh_from_db()
    assert user.marketing_opted_out_at is not None
    assert _keys(user.email) == {RESTOCK}

    _patch(client, **{RESTOCK: True})

    assert _keys(user.email) == set()
    user.refresh_from_db()
    assert user.marketing_opted_out_at is None


def test_declining_under_the_every_type_row_adds_nothing(client, user):
    optout.opt_out(
        user.email, EVERY_MARKETING_TYPE, source=EmailOptOut.Source.LINK
    )

    _patch(client, **{RESTOCK: False})

    assert _keys(user.email) == {EVERY_MARKETING_TYPE}


def test_a_lifted_opt_out_can_be_recorded_again_by_staff(client, user):
    opt_out_of_marketing(
        user.email, account_pk=user.pk, source=EmailOptOut.Source.LINK
    )
    _patch(client, **dict.fromkeys(_marketing_keys(), True))

    recorded = opt_out_of_marketing(
        user.email, account_pk=user.pk, source=EmailOptOut.Source.STAFF
    )

    assert recorded is True


def test_a_customer_changes_only_their_own_address(client, user):
    other = UserAccountFactory(num_addresses=0)
    optout.opt_out(
        other.email, EVERY_MARKETING_TYPE, source=EmailOptOut.Source.LINK
    )

    response = client.patch(
        _url(),
        {
            "types": [{"key": RESTOCK, "enabled": True}],
            "email": other.email,
            "address": other.email,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert _keys(other.email) == {EVERY_MARKETING_TYPE}
    assert _keys(user.email) == set()


@pytest.mark.parametrize(
    "key", ["order.shipped", "newsletter.confirmation", "no.such.type", "*"]
)
def test_a_key_that_is_not_marketing_is_a_400(client, user, key):
    response = _patch(client, **{key: False})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["code"] == "unknown_email_type"
    assert _keys(user.email) == set()


def test_a_type_the_store_does_not_offer_is_a_400(client):
    EmailSwitch.objects.create(type_key=RESTOCK)

    response = _patch(client, **{RESTOCK: False})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["code"] == "unknown_email_type"


def test_a_repeated_key_is_a_400(client):
    response = client.patch(
        _url(),
        {
            "types": [
                {"key": RESTOCK, "enabled": False},
                {"key": RESTOCK, "enabled": True},
            ]
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["code"] == "duplicate_email_type"
