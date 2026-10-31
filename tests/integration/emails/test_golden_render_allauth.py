"""The allauth emails as customers read them.

Characterisation, like ``test_golden_render_domains``: each case calls
``UserAccountAdapter.send_mail`` the way allauth does (inside the visitor's
request, with the context its flow builds), lets the queued task run, and
compares the message that reaches ``mail.outbox`` (subject, recipient, From,
Reply-To, headers, HTML and text, in Greek and English) with the files in
``golden/``. A deliberate change re-records them in the same commit
(``tests/utils/golden.py``).

Every allauth mail has a case: ``test_every_type_has_a_golden_case`` fails
for a type added to ``user.emails.ALLAUTH_TYPES`` with none.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from allauth.core.context import request_context
from django.core import mail
from django.test import RequestFactory
from django.utils import translation
from django.utils.translation import gettext

from tests.utils.golden import assert_golden, snapshot
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)
from user.adapter import UserAccountAdapter
from user.emails import ALLAUTH_TYPES
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

GOLDEN = Path(__file__).parent / "golden"

STORE_INBOX = "owner@store.test"
CUSTOMER_INBOX = "customer@example.test"
NOW = datetime(2026, 3, 14, 10, 30, tzinfo=UTC)
SHOP = "https://shop.example"

#: What allauth's flows put in the context besides the account.
NOTICE = {
    "timestamp": NOW,
    "ip": "203.0.113.7",
    "user_agent": "Mozilla/5.0 (golden browser)",
}

#: allauth's template prefix, then the flow's own context.
CASES: dict[str, dict[str, object]] = {
    "account/email/email_confirmation_signup": {
        "user": True,
        "activate_url": f"{SHOP}/account/verify-email/golden-key",
    },
    "account/email/email_confirmation": {"user": True, "code": "123456"},
    "account/email/password_reset_key": {
        "user": True,
        "password_reset_url": f"{SHOP}/account/password/reset/key/1-golden",
        "username": "maria",
    },
    "account/email/password_reset_code": {"code": "123456"},
    "account/email/password_reset": NOTICE,
    "account/email/password_changed": NOTICE,
    "account/email/password_set": NOTICE,
    "account/email/email_changed": {
        **NOTICE,
        "from_email": "maria@example.test",
        "to_email": "maria@example.org",
    },
    "account/email/email_deleted": {
        **NOTICE,
        "deleted_email": "maria@example.org",
    },
    "account/email/login_code": {"code": "123456"},
    "account/email/unknown_account": {"signup_url": f"{SHOP}/account/signup"},
    "account/email/account_already_exists": {
        "signup_url": f"{SHOP}/account/signup",
        "password_reset_url": f"{SHOP}/account/password/reset",
    },
    "mfa/email/recovery_codes_generated": NOTICE,
    "mfa/email/totp_activated": NOTICE,
    "mfa/email/totp_deactivated": NOTICE,
    "mfa/email/webauthn_added": NOTICE,
    "mfa/email/webauthn_removed": NOTICE,
    "socialaccount/email/account_connected": {**NOTICE, "provider": "Google"},
    "socialaccount/email/account_disconnected": {
        **NOTICE,
        "provider": "Google",
    },
}


@pytest.fixture(params=["el", "en"])
def language(request):
    return request.param


@pytest.fixture(autouse=True)
def _greek_catalogue_is_compiled():
    with translation.override("el"):
        assert gettext("Your sign-in code") != "Your sign-in code", (
            "run `uv run python manage.py compilemessages --ignore=.venv`"
        )


@pytest.fixture(autouse=True)
def _store(settings, language):
    """A bound store in the language under test with a fixed platform
    sender, storefront and API host."""
    settings.NUXT_BASE_URL = SHOP
    settings.API_BASE_URL = "https://api.shop.example"
    settings.DEFAULT_FROM_EMAIL = "noreply@platform.test"
    previous = bind_store_tenant(
        store_tenant(
            "acme",
            default_locale=language,
            store_name="Acme Gadgets",
            contact_email=STORE_INBOX,
            owner_email=STORE_INBOX,
        )
    )
    yield
    unbind_store_tenant(previous)
    mail.outbox.clear()


def test_every_type_has_a_golden_case():
    assert set(CASES) == set(ALLAUTH_TYPES)


def send(prefix: str, language: str):
    """Send *prefix* as allauth does, in the visitor's *language*."""
    flow = dict(CASES[prefix])
    if flow.pop("user", False):
        flow["user"] = UserAccountFactory(
            num_addresses=0,
            email=CUSTOMER_INBOX,
            first_name="Maria",
            last_name="Papadopoulou",
            username="maria",
            id=500_001,
            language_code=language,
        )
    request = RequestFactory().post(
        "/_allauth/app/v1/auth/login", HTTP_X_LANGUAGE=language
    )
    mail.outbox.clear()
    with request_context(request):
        UserAccountAdapter(request).send_mail(prefix, CUSTOMER_INBOX, flow)
    [message] = mail.outbox
    return message


@pytest.mark.parametrize("prefix", CASES)
def test_the_mail_is_what_the_customer_reads(prefix, language):
    message = send(prefix, language)

    name = ALLAUTH_TYPES[prefix].replace(".", "_")
    assert_golden(GOLDEN, name, language, snapshot(message))
