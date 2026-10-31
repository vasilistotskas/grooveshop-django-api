"""allauth's own flows deliver their mail through the email pipeline.

The golden tests call the adapter with a context allauth would build; these
drive the real headless endpoints, so a change to what a flow passes to the
adapter (a renamed context key, a new prefix) fails here and not as a
missing or unbranded mail in production. Each mail is the type allauth's
prefix names: store From, the recipient's language, list headers, and the
HTML the store's theme renders.
"""

from __future__ import annotations

import json

import pytest
from django.core import mail
from django.test import Client
from rest_framework import status

from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)
from user.factories.account import UserAccountFactory

BASE = "/_allauth/app/v1"
PASSWORD = "S3cure-pass-phrase!"
STORE = "Acme Gadgets"

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _store(settings):
    settings.NUXT_BASE_URL = "https://shop.example"
    settings.DEFAULT_FROM_EMAIL = "noreply@platform.test"
    previous = bind_store_tenant(
        store_tenant(
            "acme",
            store_name=STORE,
            contact_email="owner@store.test",
            default_locale="en",
        )
    )
    mail.outbox.clear()
    yield
    unbind_store_tenant(previous)


@pytest.fixture
def client():
    return Client()


def post(client, path, body, *, language="en", token=None):
    headers = {"HTTP_X_LANGUAGE": language}
    if token:
        headers["HTTP_X_SESSION_TOKEN"] = token
    return client.post(
        f"{BASE}{path}",
        data=json.dumps(body),
        content_type="application/json",
        **headers,
    )


def assert_store_mail(message, *, to: str, list_label: str) -> None:
    assert message.to == [to]
    assert message.from_email == f"{STORE} <noreply@platform.test>"
    assert message.extra_headers["List-ID"].startswith(f"<{list_label}.")
    [(html, mimetype)] = message.alternatives
    assert mimetype == "text/html"
    assert STORE in html
    assert STORE in message.body


def test_sign_up_sends_the_confirmation_code(client):
    response = post(
        client,
        "/auth/signup",
        {"email": "new.shopper@example.com", "password": PASSWORD},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    [message] = mail.outbox
    assert_store_mail(
        message,
        to="new.shopper@example.com",
        list_label="account-email_confirmation_signup",
    )
    assert message.subject == "Please confirm your email address"


def test_a_password_reset_link_is_in_the_recipients_language(client):
    user = UserAccountFactory(
        num_addresses=1, email="shopper@example.com", language_code="en"
    )

    # The visitor browses in the other language: the account's wins.
    response = post(
        client, "/auth/password/request", {"email": user.email}, language="el"
    )

    assert response.status_code == status.HTTP_200_OK
    [message] = mail.outbox
    assert_store_mail(
        message, to=user.email, list_label="account-password_reset_key"
    )
    assert "/en/account/password/reset/key/" in message.body


def test_a_reset_request_for_an_unknown_address_gets_the_unknown_account_mail(
    client,
):
    response = post(
        client, "/auth/password/request", {"email": "nobody@example.com"}
    )

    assert response.status_code == status.HTTP_200_OK
    [message] = mail.outbox
    assert_store_mail(
        message,
        to="nobody@example.com",
        list_label="account-unknown_account",
    )
    assert "/en/account/signup" in message.body


def test_the_login_code_is_sent_in_the_visitors_language(client):
    user = UserAccountFactory(email="shopper@example.com")

    response = post(
        client, "/auth/code/request", {"email": user.email}, language="el"
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    [message] = mail.outbox
    assert_store_mail(message, to=user.email, list_label="account-login_code")
    assert message.subject == "Ο κωδικός σύνδεσής σας"
