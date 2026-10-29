"""Every allauth email names the STORE that sends it, text and HTML.

allauth's stock text templates (``account/email/base_message.txt`` and the
confirmation message) print ``current_site.name`` and ``current_site.domain``,
which allauth's own ``send_mail`` fills with ``get_current_site(request)``.
``UserAccountAdapter.send_mail`` replaces that method and merges the tenant's
``build_email_context`` instead, so on production webside's account emails
read "Γεια σας από το !" and "Ευχαριστούμε που χρησιμοποιείτε το !": the
text part of every account email, and the whole body of the MFA and
social-account notifications, which had no HTML twin at all.

``get_current_site`` is not the fix: ``SITE_ID`` points at one ``Site`` row
(Webside), so every store would introduce itself as Webside. The templates
read the tenant's ``SITE_NAME`` / ``SITE_URL`` instead.

The email kinds are DISCOVERED from the installed allauth, not listed here:
a kind a future allauth adds is rendered by this test the day it ships.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import allauth
import pytest
from allauth.core.context import request_context
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.test import RequestFactory
from django.utils import timezone

from user.adapter import UserAccountAdapter
from user.factories.account import UserAccountFactory

STORE_NAME = "Acme Gadgets"
STORE_URL = "https://acme.example"

ALLAUTH_TEMPLATES = Path(allauth.__file__).parent / "templates"

ALLAUTH_ROOT = Path(allauth.__file__).parent
_ALLAUTH_SOURCE = "".join(
    path.read_text(encoding="utf-8")
    for path in ALLAUTH_ROOT.rglob("*.py")
    if "tests" not in path.parts
)


def _is_sent_by_allauth(prefix: str) -> bool:
    """``email_confirm_message.txt`` still ships but no flow sends it: a
    kind counts only while allauth's code names its prefix."""
    return f'"{prefix}"' in _ALLAUTH_SOURCE


# Every email template allauth ships AND sends, as the prefix its adapter
# is given: ``account/email/login_code_message.txt`` is sent as
# ``account/email/login_code``. ``base_message`` / ``base_notification``
# are layouts the others extend, not emails.
ALLAUTH_EMAIL_PREFIXES = sorted(
    prefix
    for prefix in (
        path.relative_to(ALLAUTH_TEMPLATES)
        .as_posix()
        .removesuffix("_message.txt")
        for path in ALLAUTH_TEMPLATES.glob("*/email/*_message.txt")
        if not path.name.startswith("base_")
    )
    if _is_sent_by_allauth(prefix)
)

# What allauth's flows put in the context, as a superset: a template
# reads the keys it needs and ignores the rest.
FLOW_CONTEXT = {
    "code": "123456",
    "activate_url": "https://acme.example/verify-email/KEY",
    "password_reset_url": "https://acme.example/reset/KEY",
    "signup_url": "https://acme.example/signup",
    "from_email": "old@example.com",
    "to_email": "new@example.com",
    "deleted_email": "gone@example.com",
    "provider": "Google",
    "ip": "203.0.113.7",
    "user_agent": "pytest",
    "username": "someone",
}

# What an unrendered store name leaves behind, in both languages the
# templates are translated to: the sentence around the name, with the
# name missing.
EMPTY_NAME_PHRASES = (
    "Hello from !",
    "using !",
    "Γεια σας από το !",
    "χρησιμοποιείτε το !",
    "account on .",
    "λογαριασμό στο .",
)


@pytest.fixture(autouse=True)
def _store(settings, bind_tenant):
    settings.NUXT_BASE_URL = STORE_URL
    bind_tenant(SimpleNamespace(schema_name="acme", store_name=STORE_NAME))
    mail.outbox = []


def _send(prefix: str, language: str):
    user = UserAccountFactory(num_addresses=0, language_code=language)
    request = RequestFactory().post("/_allauth/app/v1/auth/login")
    request.user = AnonymousUser()
    context = {
        **FLOW_CONTEXT,
        "user": user,
        "timestamp": timezone.now(),
        "email": user.email,
    }
    mail.outbox = []
    with request_context(request):
        UserAccountAdapter(request).send_mail(prefix, user.email, context)
    assert len(mail.outbox) == 1
    return mail.outbox[0]


def test_every_allauth_email_kind_is_covered():
    """The discovery found the kinds this PR knows about, plus whatever
    the installed allauth adds: guard against a glob that silently
    matches nothing."""
    assert "account/email/login_code" in ALLAUTH_EMAIL_PREFIXES
    assert "mfa/email/totp_activated" in ALLAUTH_EMAIL_PREFIXES
    assert "socialaccount/email/account_connected" in ALLAUTH_EMAIL_PREFIXES
    assert "account/email/password_reset_code" in ALLAUTH_EMAIL_PREFIXES
    assert "account/email/email_confirm" not in ALLAUTH_EMAIL_PREFIXES


@pytest.mark.django_db
@pytest.mark.parametrize("language", ["en", "el"])
@pytest.mark.parametrize("prefix", ALLAUTH_EMAIL_PREFIXES)
class TestAllauthEmailsNameTheStore:
    def test_text_part_names_the_store_and_links_to_it(self, prefix, language):
        message = _send(prefix, language)

        assert STORE_NAME in message.body
        assert STORE_URL in message.body
        for phrase in EMPTY_NAME_PHRASES:
            assert phrase not in message.body

    def test_html_part_exists_and_names_the_store(self, prefix, language):
        """No allauth email is plain text only: the MFA and social
        notifications had no HTML at all."""
        message = _send(prefix, language)

        assert len(message.alternatives) == 1, (
            f"{prefix} has no text/html twin under core/templates"
        )
        html, mimetype = message.alternatives[0]
        assert mimetype == "text/html"
        assert STORE_NAME in html
        assert "email-wrapper" in html
        for phrase in EMPTY_NAME_PHRASES:
            assert phrase not in html

    def test_subject_carries_the_store_name_too(self, prefix, language):
        assert _send(prefix, language).subject.startswith(f"[{STORE_NAME}]")


@pytest.mark.django_db
def test_text_part_does_not_borrow_the_site_row(db):
    """``SITE_ID`` is Webside: the text part must not introduce any other
    store under that name."""
    message = _send("account/email/login_code", "en")

    assert "Webside" not in message.body
    assert re.search(rf"Hello from {STORE_NAME}!", message.body)
