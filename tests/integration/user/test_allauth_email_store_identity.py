"""Every allauth email names the STORE that sends it, text and HTML.

allauth's stock text templates print ``current_site.name`` and
``current_site.domain`` (``get_current_site(request)``), which on production
webside's account emails rendered as "Γεια σας από το !": the text part of
every account email, and the whole body of the MFA and social-account
notifications, which had no HTML twin at all.

``get_current_site`` is not the fix: ``SITE_ID`` points at one ``Site`` row
(Webside), so every store would introduce itself as Webside. Each allauth mail
is an email type (``user.emails.ALLAUTH_TYPES``) with its own templates, which
read the tenant's ``SITE_NAME`` / ``SITE_URL``.

The email kinds are DISCOVERED from the installed allauth, not listed here:
a kind a future allauth adds fails ``TestEveryKindIsAnEmailType`` the day it
ships, instead of going out unbranded.
"""

from __future__ import annotations

import re
from pathlib import Path

import allauth
import pytest
from allauth.core.context import request_context
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.test import RequestFactory
from django.utils import timezone, translation
from django.utils.translation import gettext

from emails.registry import registry
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)
from user.adapter import UserAccountAdapter
from user.emails import ALLAUTH_TYPES
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
def _store(settings, db):
    """A real store row, bound: the mail is sent from a task, which enters
    the store's schema and reads the row back by its name."""
    settings.NUXT_BASE_URL = STORE_URL
    previous = bind_store_tenant(
        store_tenant("acme", store_name=STORE_NAME, default_locale="en")
    )
    mail.outbox = []
    yield
    unbind_store_tenant(previous)


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

    def test_subject_is_the_types_subject_in_the_recipients_language(
        self, prefix, language
    ):
        msgid = registry.get(ALLAUTH_TYPES[prefix]).subject
        with translation.override(language):
            expected = gettext(msgid)

        assert _send(prefix, language).subject == expected

    def test_the_from_names_the_store(self, prefix, language):
        assert STORE_NAME in _send(prefix, language).from_email


@pytest.mark.django_db
def test_text_part_does_not_borrow_the_site_row(db):
    """``SITE_ID`` is Webside: the text part must not introduce any other
    store under that name."""
    message = _send("account/email/login_code", "en")

    assert "Webside" not in message.body
    assert re.search(rf"^{STORE_NAME}$", message.body, re.MULTILINE)


class TestEveryKindIsAnEmailType:
    def test_every_kind_allauth_sends_is_mapped(self):
        """An unmapped prefix raises ``KeyError`` in the adapter: this
        fails the build before a new allauth mail goes out unbranded."""
        assert set(ALLAUTH_EMAIL_PREFIXES) <= set(ALLAUTH_TYPES)

    def test_no_mapping_is_left_for_a_kind_allauth_no_longer_sends(self):
        assert set(ALLAUTH_TYPES) <= set(ALLAUTH_EMAIL_PREFIXES)

    def test_every_mapped_type_is_registered_and_required(self):
        for key in ALLAUTH_TYPES.values():
            assert registry.get(key).required
