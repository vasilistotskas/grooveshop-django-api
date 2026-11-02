"""The control plane's emails pages, on the real public schema.

The platform's own mail (billing notices, the staff password reset, the
health and backup alerts) is sent and logged under ``public``. The main
suite has one schema, so what the platform page lists, which log it reads
and that a store's rows never reach it are proved here, with real schemas.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.urls import reverse
from django.utils import translation
from django.utils.html import escape
from django.utils.translation import gettext, ngettext
from django_tenants.test.client import TenantClient
from django_tenants.utils import get_public_schema_name, schema_context

from emails import figures, optout
from emails.models import EmailLog, EmailSuppression
from emails.registry import registry
from emails.status import Status
from emails.types import Owner
from tenant.auth_backends import PLATFORM_STAFF_BACKEND_PATH

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

PLATFORM_DOMAIN = "platform-email-mt.test"
URLCONF = settings.PUBLIC_SCHEMA_URLCONF
PUBLIC = get_public_schema_name()
PLATFORM_KEY = "platform.health_alert"
STORE_KEY = "order.shipped"
OPERATOR_EMAIL = "mt-platform-email-operator@example.com"


def _url(name: str, *args) -> str:
    return reverse(f"platform_admin:{name}", args=args, urlconf=URLCONF)


@pytest.fixture
def platform_client(mt_public_tenant):
    from tenant.models import TenantDomain
    from user.models import UserAccount

    with schema_context(PUBLIC):
        TenantDomain.objects.get_or_create(
            domain=PLATFORM_DOMAIN,
            defaults={"tenant": mt_public_tenant, "is_primary": True},
        )
        operator, _created = UserAccount.objects.get_or_create(
            email=OPERATOR_EMAIL,
            defaults={
                "username": "mtplatformemailoperator",
                "is_staff": True,
                "is_superuser": True,
            },
        )
        client = TenantClient(mt_public_tenant)
        client.force_login(operator, backend=PLATFORM_STAFF_BACKEND_PATH)
    return client


@pytest.fixture(autouse=True)
def _clean(mt_tenant):
    cache.clear()
    mail.outbox.clear()
    yield
    for schema in (PUBLIC, mt_tenant.schema_name):
        with schema_context(schema):
            EmailSuppression.objects.all().delete()
            EmailLog.objects.all().delete()
    cache.clear()


def _log(schema: str, key: str, status=Status.SENT, **fields) -> EmailLog:
    with schema_context(schema):
        return EmailLog.objects.create(
            type_key=key,
            status=status,
            recipient_hash=optout.address_hash("someone@example.test"),
            recipient_masked="s***@e***.test",
            subject="zz-" + key,
            **fields,
        )


def _said(response, msgid: str) -> str:
    """``msgid`` as the page printed it: translated, then HTML-escaped."""
    with translation.override(response["Content-Language"]):
        return escape(gettext(msgid))


def test_the_page_lists_the_platforms_emails_and_none_of_a_stores(
    platform_client,
):
    response = platform_client.get(_url("emails_emailswitch_changelist"))

    html = response.content.decode()
    assert response.status_code == 200
    for email_type in registry.types_for(Owner.PLATFORM):
        assert f'id="email-{email_type.key}"' in html, email_type.key
    for email_type in registry.types_for(Owner.STORE):
        assert f'id="email-{email_type.key}"' not in html, email_type.key
    # Nothing here can be switched, and the audiences are the groups.
    assert 'name="enabled"' not in html
    assert _said(response, "To the platform's operators") in html


def test_the_figures_are_the_public_schemas_only(platform_client, mt_tenant):
    _log(PUBLIC, PLATFORM_KEY, Status.BOUNCED)
    _log(mt_tenant.schema_name, PLATFORM_KEY, Status.SENT)
    _log(mt_tenant.schema_name, STORE_KEY, Status.FAILED)

    response = platform_client.get(_url("emails_emailswitch_changelist"))
    html = response.content.decode()

    article = html.split(f'id="email-{PLATFORM_KEY}"')[1].split("</article>")[0]
    # In the language the page was answered in (the platform admin is Greek
    # unless an operator chose another).
    with translation.override(response["Content-Language"]):
        sent = ngettext(
            "%(count)s sent in 30 days", "%(count)s sent in 30 days", 1
        ) % {"count": 1}
        failed = ngettext("%(count)s failed", "%(count)s failed", 1) % {
            "count": 1
        }
    assert sent in article
    assert failed in article


def test_a_cached_figure_is_never_served_to_another_schema(mt_tenant):
    """The query is cached under a key that carries the schema, so the
    platform's page and a store's page never share an answer."""
    _log(PUBLIC, PLATFORM_KEY, Status.SENT)
    with schema_context(PUBLIC):
        assert PLATFORM_KEY in figures.type_figures.get()
    with schema_context(mt_tenant.schema_name):
        assert figures.type_figures.get() == {}
    _log(mt_tenant.schema_name, STORE_KEY, Status.SENT)
    cache.clear()
    with schema_context(mt_tenant.schema_name):
        assert set(figures.type_figures.get()) == {STORE_KEY}
    with schema_context(PUBLIC):
        assert STORE_KEY not in figures.type_figures.get()


def test_the_log_is_the_public_schemas_and_filters_platform_emails(
    platform_client, mt_tenant
):
    _log(PUBLIC, PLATFORM_KEY)
    _log(mt_tenant.schema_name, STORE_KEY)

    response = platform_client.get(_url("emails_emaillog_changelist"))

    html = response.content.decode()
    assert response.status_code == 200
    assert "zz-platform.health_alert" in html
    assert "zz-order.shipped" not in html


def test_the_suppression_list_is_the_public_schemas(platform_client, mt_tenant):
    for schema, masked in (
        (PUBLIC, "p***@e***.test"),
        (mt_tenant.schema_name, "m***@e***.test"),
    ):
        with schema_context(schema):
            EmailSuppression.objects.create(
                address_hash=optout.address_hash(masked),
                recipient_masked=masked,
                reason=EmailSuppression.Reason.HARD_BOUNCE,
                scope=EmailSuppression.Scope.ALL,
            )

    html = platform_client.get(
        _url("emails_emailsuppression_changelist")
    ).content.decode()

    assert "p***@e***.test" in html
    assert "m***@e***.test" not in html


def test_a_platform_email_has_a_page_and_a_store_email_has_none(
    platform_client,
):
    assert (
        platform_client.get(
            _url("emails_emailswitch_type", PLATFORM_KEY)
        ).status_code
        == 200
    )
    assert (
        platform_client.get(
            _url("emails_emailswitch_type", STORE_KEY)
        ).status_code
        == 404
    )


def test_the_preview_renders_a_platform_email_and_offers_no_orders(
    platform_client,
):
    page = platform_client.get(_url("emails_emailswitch_type", PLATFORM_KEY))
    assert 'name="order"' not in page.content.decode()

    rendered = platform_client.post(
        _url("emails_emailswitch_preview_render"),
        {"type_key": PLATFORM_KEY, "language": "en", "format": "text"},
    )

    assert rendered.status_code == 200
    assert "<pre" in rendered.content.decode()


def test_a_crafted_order_is_ignored_on_the_platform(platform_client):
    """The orders table does not exist in ``public``: a posted id must not
    reach it, whoever the operator is."""
    rendered = platform_client.post(
        _url("emails_emailswitch_preview_render"),
        {
            "type_key": PLATFORM_KEY,
            "language": "en",
            "format": "text",
            "order": "1",
        },
    )

    assert rendered.status_code == 200


def test_send_test_goes_to_the_operator_and_is_logged_in_public(
    platform_client, mt_tenant
):
    response = platform_client.post(
        _url("emails_emailswitch_test_send"),
        {
            "type_key": PLATFORM_KEY,
            "language": "en",
            "to": "victim@example.test",
        },
    )

    assert response.status_code == 302
    [message] = mail.outbox
    assert message.to == [OPERATOR_EMAIL]
    assert message.subject.startswith("[Test] ")
    with schema_context(PUBLIC):
        row = EmailLog.objects.get(type_key=PLATFORM_KEY)
    assert row.is_test is True
    assert row.status == Status.SENT
    with schema_context(mt_tenant.schema_name):
        assert not EmailLog.objects.exists()


def test_send_test_is_throttled_per_operator(platform_client, settings):
    settings.EMAIL_TEST_SENDS_PER_HOUR = 1
    url = _url("emails_emailswitch_test_send")
    data = {"type_key": PLATFORM_KEY, "language": "en"}

    platform_client.post(url, data)
    platform_client.post(url, data)

    assert len(mail.outbox) == 1


def test_a_store_email_cannot_be_tested_from_the_platform(platform_client):
    response = platform_client.post(
        _url("emails_emailswitch_test_send"),
        {"type_key": STORE_KEY, "language": "en"},
    )

    assert response.status_code == 404
    assert mail.outbox == []


def test_nothing_can_be_switched_on_the_platform(platform_client):
    response = platform_client.post(
        _url("emails_emailswitch_toggle"), {"type_key": PLATFORM_KEY}
    )

    assert response.status_code == 404


def test_the_dashboard_flags_failing_platform_mail(platform_client, mt_tenant):
    _log(mt_tenant.schema_name, STORE_KEY, Status.FAILED)  # a store's: not ours
    response = platform_client.get(_url("index"))
    title = _said(response, "The platform's emails are failing")
    assert title not in response.content.decode()

    _log(PUBLIC, PLATFORM_KEY, Status.FAILED)
    response = platform_client.get(_url("index"))
    html = response.content.decode()

    assert title in html
    assert "failing=7" in html


def test_the_sidebar_offers_the_page(platform_client):
    html = platform_client.get(_url("index")).content.decode()

    assert _url("emails_emailswitch_changelist") in html


def test_the_email_filter_offers_the_schemas_own_emails(mt_tenant):
    """One admin class serves both sites; which emails it filters by is the
    schema in use."""
    from emails.admin import serving_owner

    with schema_context(PUBLIC):
        assert serving_owner() is Owner.PLATFORM
    with schema_context(mt_tenant.schema_name):
        assert serving_owner() is Owner.STORE


def test_the_log_filters_by_one_platform_email(platform_client):
    _log(PUBLIC, PLATFORM_KEY)
    _log(PUBLIC, "platform.backup_failed")

    html = platform_client.get(
        _url("emails_emaillog_changelist"), {"type_key": PLATFORM_KEY}
    ).content.decode()

    assert "zz-platform.health_alert" in html
    assert "zz-platform.backup_failed" not in html


def test_the_platform_page_describes_the_platforms_emails_not_a_stores(
    platform_client,
):
    from django.utils.html import escape

    from emails.admin import EmailSwitchAdmin, PlatformEmailSwitchAdmin

    response = platform_client.get(_url("emails_emailswitch_changelist"))

    html = response.content.decode()
    with translation.override(response["Content-Language"]):
        assert escape(str(PlatformEmailSwitchAdmin.intro)) in html
        assert escape(str(EmailSwitchAdmin.intro)) not in html
