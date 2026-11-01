"""``POST /api/v1/emails/unsubscribe/<token>/all``: the storefront page's
"stop all marketing emails from the store"."""

from __future__ import annotations

import pytest
from django.core import signing
from django.db import connection
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from emails import optout
from emails.models import EVERY_MARKETING_TYPE, EmailOptOut
from user.factories.account import UserAccountFactory
from user.factories.subscription import UserSubscriptionFactory
from user.models.subscription import UserSubscription

pytestmark = pytest.mark.django_db

REENGAGEMENT = "user.reengagement"
GUEST = "guest@example.test"
ACTIVE = UserSubscription.SubscriptionStatus.ACTIVE


def _url(token: str) -> str:
    return reverse("email-unsubscribe-all", kwargs={"token": token})


def _post(token: str):
    return APIClient().post(_url(token))


def test_the_path_is_the_type_url_plus_all():
    token = optout.make_token(GUEST, REENGAGEMENT)

    assert _url(token) == f"{optout.UNSUBSCRIBE_PATH}/{token}/all"


def test_an_account_token_carries_the_pk_and_no_address():
    user = UserAccountFactory(num_addresses=0)

    token = optout.make_token(user.email, REENGAGEMENT)

    assert optout.read_token(token).account_pk == user.pk
    payload = signing.loads(token, salt=optout.TOKEN_SALT)
    assert user.email not in str(payload)
    assert payload["a"] == user.pk


def test_a_guest_token_carries_no_account():
    assert optout.read_token(optout.make_token(GUEST, REENGAGEMENT)) == (
        optout.TokenClaims(
            address_hash=optout.address_hash(GUEST),
            type_key=REENGAGEMENT,
            account_pk=None,
        )
    )


def test_it_opts_an_account_out_of_everything_and_ends_its_subscriptions():
    subscription = UserSubscriptionFactory(status=ACTIVE)
    user = subscription.user
    token = optout.make_token(user.email, REENGAGEMENT)

    response = _post(token)

    assert response.status_code == status.HTTP_200_OK
    assert not response.content
    row = EmailOptOut.objects.get()
    assert row.type_key == EVERY_MARKETING_TYPE
    assert row.address_hash == optout.address_hash(user.email)
    assert row.source == EmailOptOut.Source.LINK
    user.refresh_from_db()
    assert user.marketing_opted_out_at is not None
    subscription.refresh_from_db()
    assert (
        subscription.status == UserSubscription.SubscriptionStatus.UNSUBSCRIBED
    )


def test_it_leaves_other_accounts_alone():
    leaver = UserAccountFactory(num_addresses=0)
    bystander_subscription = UserSubscriptionFactory(status=ACTIVE)

    _post(optout.make_token(leaver.email, REENGAGEMENT))

    bystander_subscription.refresh_from_db()
    assert bystander_subscription.status == ACTIVE
    bystander_subscription.user.refresh_from_db()
    assert bystander_subscription.user.marketing_opted_out_at is None


def test_a_guest_records_the_opt_out_row_only():
    bystander = UserAccountFactory(num_addresses=0)

    response = _post(optout.make_token(GUEST, REENGAGEMENT))

    assert response.status_code == status.HTTP_200_OK
    row = EmailOptOut.objects.get()
    assert row.type_key == EVERY_MARKETING_TYPE
    assert row.address_hash == optout.address_hash(GUEST)
    bystander.refresh_from_db()
    assert bystander.marketing_opted_out_at is None


def test_it_takes_a_token_that_already_names_every_type():
    token = optout.make_token(GUEST, EVERY_MARKETING_TYPE)

    assert _post(token).status_code == status.HTTP_200_OK
    assert EmailOptOut.objects.get().type_key == EVERY_MARKETING_TYPE


def test_a_repeat_is_the_same_200_and_one_row_with_the_first_time_kept():
    user = UserAccountFactory(num_addresses=0)
    token = optout.make_token(user.email, REENGAGEMENT)
    _post(token)
    user.refresh_from_db()
    first = user.marketing_opted_out_at

    assert _post(token).status_code == status.HTTP_200_OK

    user.refresh_from_db()
    assert user.marketing_opted_out_at == first
    assert EmailOptOut.objects.count() == 1


def test_the_type_url_stays_type_only():
    user = UserAccountFactory(num_addresses=0)
    subscription = UserSubscriptionFactory(user=user, status=ACTIVE)
    token = optout.make_token(user.email, REENGAGEMENT)

    response = APIClient().post(
        reverse("email-unsubscribe", kwargs={"token": token})
    )

    assert response.status_code == status.HTTP_200_OK
    assert EmailOptOut.objects.get().type_key == REENGAGEMENT
    user.refresh_from_db()
    assert user.marketing_opted_out_at is None
    subscription.refresh_from_db()
    assert subscription.status == ACTIVE


def _tokens() -> dict[str, str]:
    return {
        "garbage": "garbage",
        "another store": signing.dumps(
            {"schema": "elsewhere", "h": "x", "t": REENGAGEMENT},
            salt=optout.TOKEN_SALT,
        ),
        "another salt": signing.dumps(
            {"schema": connection.schema_name, "h": "x", "t": REENGAGEMENT},
            salt="another.salt",
        ),
    }


@pytest.mark.parametrize("kind", ["garbage", "another store", "another salt"])
def test_a_bad_token_is_the_usual_400_and_changes_nothing(kind):
    response = _post(_tokens()[kind])

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["code"] == "unsubscribe_token_invalid"
    assert not EmailOptOut.objects.exists()
