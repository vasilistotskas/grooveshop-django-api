"""The store admin's email pages: status, one email, the log, the
suppression list and the test send.

Rendered through the real admin site with a store's staff logged in (the
role system's rules, no permission is patched), as ``test_switch_admin`` does.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.contrib.admin.models import DELETION, LogEntry
from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone, translation

from admin.dashboard.store import queries
from emails import optout
from emails.figures import SENT_STATUSES
from emails.models import EmailEvent, EmailLog, EmailSuppression, EmailSwitch
from emails.registry import registry
from emails.status import Status
from emails.testsend import Throttled, send_test
from tenant.models import TenantMembershipRole
from tests.integration.texts.conftest import staff_client
from tests.utils.emails import probe
from tests.utils.staff import store_staff

pytestmark = pytest.mark.django_db

KEY = "order.shipped"
ADDRESS = "customer@example.test"


@pytest.fixture(autouse=True)
def _fresh(settings):
    cache.clear()
    settings.EMAIL_TEST_SENDS_PER_HOUR = 10
    mail.outbox.clear()
    with translation.override("en"):
        yield


@pytest.fixture
def admin(tenant):
    return staff_client(tenant, TenantMembershipRole.ADMIN)


@pytest.fixture
def staff(tenant):
    return staff_client(tenant, TenantMembershipRole.STAFF)


def page(name: str = "changelist", *args) -> str:
    return reverse(f"admin:emails_emailswitch_{name}", args=args)


def log(
    status=Status.SENT,
    *,
    key=KEY,
    days_ago: int = 0,
    address: str = ADDRESS,
    subject: str = "A subject",
    **fields,
) -> EmailLog:
    left = status in SENT_STATUSES
    row = EmailLog.objects.create(
        type_key=key,
        status=status,
        recipient_hash=optout.address_hash(address),
        recipient_masked="c***@e***.test",
        subject=subject,
        sent_at=timezone.now() - timedelta(days=days_ago) if left else None,
        **fields,
    )
    EmailLog.objects.filter(pk=row.pk).update(
        created_at=timezone.now() - timedelta(days=days_ago)
    )
    return row


def article(html: str, key: str) -> str:
    return html.split(f'id="email-{key}"')[1].split("</article>")[0]


class TestTheStatusPage:
    def test_a_row_shows_what_it_sent_and_how_many_failed(self, admin):
        client, _ = admin
        log(Status.SENT, days_ago=2)
        log(Status.DELIVERED, days_ago=1)
        log(Status.BOUNCED, days_ago=3)
        log(Status.SENT, days_ago=40)  # outside the 30 days
        log(Status.SKIPPED_OPTED_OUT)  # nothing left the store
        log(Status.SENT, is_test=True)  # a test is not a send

        html = article(client.get(page()).content.decode(), KEY)

        assert "3 sent in 30 days" in html
        assert "1 failed" in html
        assert "Last sent 1\xa0day ago" in html
        assert "type_key=order.shipped" in html
        assert "failing=30" in html

    def test_an_email_never_sent_says_so(self, admin):
        client, _ = admin

        html = article(client.get(page()).content.decode(), KEY)

        assert "Not sent in the last 30 days" in html
        assert "0 sent in 30 days" in html
        assert "failed" not in html

    def test_an_email_with_no_switch_row_and_no_gate_is_on(
        self, admin, catalogue
    ):
        client, _ = admin
        catalogue(probe("probe.notice", area="orders"))

        html = article(client.get(page()).content.decode(), "probe.notice")

        assert re.search(r">\s*On\s*<", html)
        assert "Switched off" not in html

    def test_a_switched_off_email_says_so(self, admin, catalogue):
        client, _ = admin
        catalogue(probe("probe.notice", area="orders"))
        EmailSwitch.objects.create(type_key="probe.notice")

        html = article(client.get(page()).content.decode(), "probe.notice")

        assert "Switched off" in html

    def test_a_required_email_is_required(self, admin):
        client, _ = admin

        html = article(client.get(page()).content.decode(), KEY)

        assert "Required" in html
        assert "toggle" not in html

    def test_the_tab_keeps_one_audience(self, admin):
        client, _ = admin

        customers = client.get(page()).content.decode()
        team = client.get(page(), {"tab": "team"}).content.decode()

        assert 'id="email-order.shipped"' in customers
        assert 'id="email-order.shipped"' not in team
        assert 'id="email-team.new_order"' in team
        assert 'id="email-team.new_order"' not in customers

    def test_search_keeps_the_emails_that_match(self, admin):
        client, _ = admin

        html = client.get(page(), {"q": "shipped"}).content.decode()

        assert 'id="email-order.shipped"' in html
        assert 'id="email-order.received"' not in html

    def test_the_failing_filter_keeps_only_failing_emails(self, admin):
        client, _ = admin
        log(Status.BOUNCED)

        html = client.get(page(), {"status": "failing"}).content.decode()

        assert 'id="email-order.shipped"' in html
        assert 'id="email-order.received"' not in html

    def test_the_off_filter_keeps_switched_off_and_gated_emails(self, admin):
        client, _ = admin
        EmailSwitch.objects.create(type_key="cart.checkout_abandoned")

        html = client.get(page(), {"status": "off"}).content.decode()

        assert 'id="email-cart.checkout_abandoned"' in html
        assert 'id="email-order.shipped"' not in html

    def test_nothing_matching_says_so(self, admin):
        client, _ = admin

        html = client.get(page(), {"q": "zzzzzz"}).content.decode()

        assert "No email matches." in html

    def test_it_never_shows_an_address(self, admin):
        client, _ = admin
        log(Status.BOUNCED)

        html = client.get(page()).content.decode()

        assert ADDRESS not in html

    def test_it_links_the_log_and_the_suppression_list_to_those_who_may(
        self, admin, staff
    ):
        for client, _ in (admin, staff):
            html = client.get(page()).content.decode()
            assert reverse("admin:emails_emaillog_changelist") in html
            assert reverse("admin:emails_emailsuppression_changelist") in html


class TestQueriesStayFlat:
    def _count(self, client) -> int:
        client.get(page())  # warm the cache and the session
        with CaptureQueriesContext(connection) as queries_run:
            assert client.get(page()).status_code == 200
        return len(queries_run)

    def test_the_status_page_costs_the_same_for_more_types(
        self, admin, catalogue
    ):
        client, _ = admin
        catalogue(
            *(probe(f"probe.t{index}", area="orders") for index in range(30))
        )
        for index in range(30):
            log(key=f"probe.t{index}")

        def count(**params) -> int:
            client.get(page(), params)  # warm the cache and the session
            with CaptureQueriesContext(connection) as queries_run:
                assert client.get(page(), params).status_code == 200
            return len(queries_run)

        one = count(q="probe.t7")
        thirty = count(q="probe.t")

        assert thirty == one


class TestOneEmail:
    def test_the_page_lists_the_latest_sends_with_their_timeline(self, admin):
        client, _ = admin
        sent = log(Status.DELIVERED, provider_message_id="m-1")
        EmailEvent.objects.create(
            log=sent,
            provider_event_id="e1",
            type="email.delivered",
            occurred_at=timezone.now(),
        )

        html = client.get(page("type", KEY)).content.decode()

        assert "c***@e***.test" in html
        assert "Delivered" in html
        assert "delivered" in html
        assert ADDRESS not in html
        assert 'hx-post="' + page("preview_render") + '"' in html

    def test_it_lists_at_most_twenty(self, admin):
        client, _ = admin
        for _index in range(25):
            log()

        html = client.get(page("type", KEY)).content.decode()

        assert html.count("c***@e***.test") == 20

    def test_staff_see_the_page_without_a_send_button(self, staff, admin):
        staff_html = staff[0].get(page("type", KEY)).content.decode()
        admin_html = admin[0].get(page("type", KEY)).content.decode()

        assert "Send test" not in staff_html
        assert "Send test" in admin_html


class TestSendTest:
    def post(self, client, key=KEY, **extra):
        return client.post(
            page("test_send"),
            {"type_key": key, "language": "en", **extra},
            follow=False,
        )

    def test_it_goes_only_to_the_signed_in_account(self, admin):
        client, user = admin

        response = self.post(
            client,
            to="victim@example.test",
            email="victim@example.test",
            address="victim@example.test",
            recipient="victim@example.test",
        )

        assert response.status_code == 302
        [message] = mail.outbox
        assert message.to == [user.email]
        assert "victim@example.test" not in str(message.message())

    def test_it_is_marked_as_a_test_and_names_who_sent_it(self, admin):
        client, user = admin

        self.post(client)

        row = EmailLog.objects.get()
        assert row.is_test is True
        assert row.triggered_by_staff_id == user.pk
        assert row.status == Status.SENT
        assert row.subject.startswith("[Test] ")
        assert mail.outbox[0].subject.startswith("[Test] ")

    def test_a_switched_off_email_can_still_be_tested(self, admin):
        client, _ = admin
        EmailSwitch.objects.create(type_key="cart.checkout_abandoned")

        self.post(client, key="cart.checkout_abandoned")

        assert len(mail.outbox) == 1

    def test_a_suppressed_account_address_is_still_tested(self, admin):
        client, user = admin
        EmailSuppression.objects.create(
            address_hash=optout.address_hash(user.email),
            recipient_masked="x***@y***.z",
            reason=EmailSuppression.Reason.HARD_BOUNCE,
            scope=EmailSuppression.Scope.ALL,
        )

        self.post(client)

        assert len(mail.outbox) == 1

    def test_it_is_throttled_per_staff_member(self, admin, staff, settings):
        settings.EMAIL_TEST_SENDS_PER_HOUR = 2
        client, _user = admin

        for _index in range(2):
            self.post(client)
        response = self.post(client)

        assert len(mail.outbox) == 2
        follow = client.get(response["Location"])
        assert "used all your test emails" in follow.content.decode()
        # Another staff member has their own allowance.
        other, _ = staff
        assert (
            other.post(
                page("test_send"), {"type_key": KEY, "language": "en"}
            ).status_code
            == 403
        )

    def test_the_throttle_counts_per_user(self, admin, tenant, settings):
        settings.EMAIL_TEST_SENDS_PER_HOUR = 1
        _client, user = admin
        other = store_staff(tenant, role=TenantMembershipRole.ADMIN)
        email_type = registry.get(KEY)

        send_test(user, email_type, order_id=None, language="en")
        with pytest.raises(Throttled):
            send_test(user, email_type, order_id=None, language="en")
        send_test(other, email_type, order_id=None, language="en")

    def test_staff_may_not_send_tests(self, staff):
        client, _ = staff

        assert self.post(client).status_code == 403
        assert mail.outbox == []

    def test_a_platform_email_cannot_be_tested_from_a_store(self, admin):
        client, _ = admin

        assert self.post(client, key="platform.health_alert").status_code == 404
        assert mail.outbox == []

    def test_a_get_is_not_allowed(self, admin):
        assert admin[0].get(page("test_send")).status_code == 405

    def test_a_failing_transport_is_reported_not_a_500(self, admin):
        from unittest import mock

        client, _ = admin

        with mock.patch(
            "emails.transport.EmailMultiAlternatives.send",
            side_effect=OSError("relay down"),
        ):
            response = self.post(client)

        assert response.status_code == 302
        assert EmailLog.objects.get().status == Status.FAILED
        follow = client.get(response["Location"]).content.decode()
        assert "could not be sent" in follow


class TestTheLog:
    def url(self) -> str:
        return reverse("admin:emails_emaillog_changelist")

    def test_it_lists_rows_masked_for_admin_and_staff(self, admin, staff):
        log(Status.BOUNCED)

        for client, _ in (admin, staff):
            html = client.get(self.url()).content.decode()
            assert "c***@e***.test" in html
            assert ADDRESS not in html

    def test_an_exact_address_finds_its_rows_by_hash(self, admin):
        client, _ = admin
        log(address=ADDRESS)
        log(address="someone.else@example.test", subject="zz-not-me")

        html = client.get(self.url(), {"q": ADDRESS}).content.decode()

        assert "c***@e***.test" in html
        assert "zz-not-me" not in html

    def test_the_failing_filter(self, admin):
        client, _ = admin
        log(Status.SENT, subject="zz-fine-row")
        log(Status.FAILED, subject="zz-broken-row")

        html = client.get(self.url(), {"failing": "7"}).content.decode()

        assert "zz-broken-row" in html
        assert "zz-fine-row" not in html

    def test_the_type_filter(self, admin):
        client, _ = admin
        log(key="order.shipped", subject="zz-shipped-mail")
        log(key="order.received", subject="zz-received-mail")

        html = client.get(
            self.url(), {"type_key": "order.shipped"}
        ).content.decode()

        assert "zz-shipped-mail" in html
        assert "zz-received-mail" not in html

    def test_it_is_read_only(self, admin):
        client, _ = admin
        row = log()
        change = reverse("admin:emails_emaillog_change", args=[row.pk])

        assert client.get(change).status_code == 200
        assert client.post(change, {"subject": "x"}).status_code == 403
        assert (
            client.get(reverse("admin:emails_emaillog_add")).status_code == 403
        )
        assert (
            client.get(
                reverse("admin:emails_emaillog_delete", args=[row.pk])
            ).status_code
            == 403
        )

    def test_the_detail_shows_the_events(self, admin):
        client, _ = admin
        row = log(Status.BOUNCED)
        EmailEvent.objects.create(
            log=row,
            provider_event_id="e1",
            type="email.bounced",
            occurred_at=timezone.now(),
            detail={"type": "Permanent"},
        )

        html = client.get(
            reverse("admin:emails_emaillog_change", args=[row.pk])
        ).content.decode()

        assert "email.bounced" in html
        assert ADDRESS not in html


class TestTheSuppressionList:
    def row(self) -> EmailSuppression:
        return EmailSuppression.objects.create(
            address_hash=optout.address_hash(ADDRESS),
            recipient_masked="c***@e***.test",
            reason=EmailSuppression.Reason.HARD_BOUNCE,
            scope=EmailSuppression.Scope.ALL,
        )

    def url(self) -> str:
        return reverse("admin:emails_emailsuppression_changelist")

    def test_it_lists_masked_rows(self, admin, staff):
        self.row()

        for client, _ in (admin, staff):
            html = client.get(self.url()).content.decode()
            assert "c***@e***.test" in html
            assert ADDRESS not in html

    def test_an_exact_address_finds_it_by_hash(self, admin):
        client, _ = admin
        self.row()

        found = client.get(self.url(), {"q": ADDRESS}).content.decode()
        missed = client.get(
            self.url(), {"q": "nobody@example.test"}
        ).content.decode()

        assert "c***@e***.test" in found
        assert "c***@e***.test" not in missed

    def test_the_removal_dialog_names_resends_own_list(self, admin):
        client, _ = admin
        row = self.row()

        html = client.get(
            reverse("admin:emails_emailsuppression_delete", args=[row.pk])
        ).content.decode()

        assert "Resend keeps" in html
        assert "also remove it in the Resend dashboard" in html

    def test_removal_deletes_the_row_and_logs_the_actor(self, admin):
        client, user = admin
        row = self.row()

        response = client.post(
            reverse("admin:emails_emailsuppression_delete", args=[row.pk]),
            {"post": "yes"},
        )

        assert response.status_code == 302
        assert not EmailSuppression.objects.exists()
        entry = LogEntry.objects.get(action_flag=DELETION)
        assert entry.user_id == user.pk

    def test_the_bulk_action_asks_first(self, admin):
        client, _ = admin
        row = self.row()

        response = client.post(
            self.url(),
            {
                "action": "delete_selected",
                "_selected_action": [row.pk],
            },
        )

        assert response.status_code == 200
        assert "Resend keeps" in response.content.decode()
        assert EmailSuppression.objects.exists()

    def test_staff_can_look_but_not_remove(self, staff):
        client, _ = staff
        row = self.row()
        delete = reverse("admin:emails_emailsuppression_delete", args=[row.pk])

        assert client.get(self.url()).status_code == 200
        assert client.get(delete).status_code == 403
        assert client.post(delete, {"post": "yes"}).status_code == 403
        assert EmailSuppression.objects.exists()

    def test_there_is_no_add_or_change(self, admin):
        client, _ = admin
        row = self.row()

        assert (
            client.get(reverse("admin:emails_emailsuppression_add")).status_code
            == 403
        )
        assert (
            client.post(
                reverse("admin:emails_emailsuppression_change", args=[row.pk]),
                {"scope": "marketing"},
            ).status_code
            == 403
        )


class TestTheDashboard:
    def test_it_counts_last_weeks_failures_and_not_tests_or_older(self):
        log(Status.FAILED, days_ago=1)
        log(Status.COMPLAINED, days_ago=6)
        log(Status.BOUNCED, days_ago=9)  # older than a week
        log(Status.FAILED, is_test=True)  # a test
        log(Status.SENT)  # not a failure
        cache.clear()

        assert queries.attention_counts.get()["failing_emails"] == 2

    def test_the_row_opens_the_log_filtered(self, admin):
        client, _ = admin
        log(Status.FAILED)
        cache.clear()

        html = client.get(reverse("admin:index")).content.decode()

        assert "Emails failing" in html
        assert reverse("admin:emails_emaillog_changelist") in html
        assert "failing=7" in html
