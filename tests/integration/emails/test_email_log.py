"""``send_email`` writes one ``EmailLog`` row per recipient delivery.

Sent, skipped and failed all leave a row; no row holds an address or a body;
a failure's text never reaches the row.
"""

from __future__ import annotations

from datetime import timedelta
from smtplib import SMTPAuthenticationError, SMTPServerDisconnected
from unittest import mock

import pytest
import requests
from django.core import mail
from django.db import OperationalError
from django.test import override_settings
from django.utils import timezone
from extra_settings.models import Setting

from emails import optout
from emails.gates import FeatureGate
from emails.log import (
    describe_failure,
    mask_address,
    validate_retention_days_setting,
)
from emails.models import EmailLog, EmailOptOut, EmailProvider, EmailSwitch
from emails.recipients import Recipient
from emails.send import Status, send_email
from emails.tasks import purge_expired_email_logs
from emails.types import Kind
from tests.utils.emails import probe, probe_object

pytestmark = pytest.mark.django_db

ADDRESS = "customer@example.test"


@pytest.fixture(autouse=True)
def _outbox(tenant):
    mail.outbox.clear()


class TestWrites:
    def test_a_send_leaves_one_sent_row(self, catalogue):
        catalogue(probe("order.shipped"))

        send_email("order.shipped", probe_object(language="el"))

        [row] = EmailLog.objects.all()
        assert row.type_key == "order.shipped"
        assert row.status == Status.SENT
        assert row.language == "el"
        assert row.subject == "Η παραγγελία σας #7 έχει αποσταλεί"
        assert row.provider == EmailProvider.SMTP
        assert row.provider_message_id is None
        assert row.sent_at is not None
        assert row.recipient_hash == optout.address_hash(ADDRESS)
        assert row.recipient_masked == "c***@e***.test"
        assert row.error == ""

    def test_every_recipient_gets_a_row(self, catalogue):
        catalogue(
            probe(
                recipients=lambda obj: [
                    Recipient("a@example.test", "el"),
                    Recipient("b@example.test", "en"),
                ]
            )
        )

        send_email("probe.notice", probe_object())

        assert EmailLog.objects.filter(status=Status.SENT).count() == 2
        assert EmailLog.objects.values("recipient_hash").distinct().count() == 2

    def test_a_closed_gate_is_a_row_with_the_gate_label(self, catalogue):
        catalogue(
            probe(gates=(FeatureGate(feature="gift_cards", label="Carts"),))
        )

        with mock.patch.object(FeatureGate, "is_open", return_value=False):
            send_email("probe.notice", probe_object())

        [row] = EmailLog.objects.all()
        assert row.status == Status.SKIPPED_FEATURE_OFF
        assert row.reason == "Carts"
        assert row.recipient_hash == ""
        assert row.provider == ""

    def test_a_switched_off_type_is_a_row(self, catalogue):
        catalogue(probe())
        EmailSwitch.objects.create(type_key="probe.notice")

        send_email("probe.notice", probe_object())

        [row] = EmailLog.objects.all()
        assert row.status == Status.SKIPPED_SWITCHED_OFF

    def test_no_recipient_is_a_row(self, catalogue):
        catalogue(probe(recipients=lambda obj: []))

        send_email("probe.notice", probe_object())

        [row] = EmailLog.objects.all()
        assert row.status == Status.SKIPPED_NO_RECIPIENT

    def test_an_opted_out_recipient_is_a_row_naming_them_by_hash(
        self, catalogue
    ):
        catalogue(probe("cart.abandoned", kind=Kind.MARKETING))
        optout.opt_out(
            ADDRESS, "cart.abandoned", source=EmailOptOut.Source.LINK
        )

        send_email("cart.abandoned", probe_object())

        [row] = EmailLog.objects.all()
        assert row.status == Status.SKIPPED_OPTED_OUT
        assert row.recipient_hash == optout.address_hash(ADDRESS)
        assert mail.outbox == []

    def test_a_failure_marks_the_row_failed_and_still_raises(self, catalogue):
        catalogue(probe())

        with (
            mock.patch(
                "emails.transport.EmailMultiAlternatives.send",
                side_effect=SMTPServerDisconnected(f"{ADDRESS} refused"),
            ),
            pytest.raises(SMTPServerDisconnected),
        ):
            send_email("probe.notice", probe_object())

        [row] = EmailLog.objects.all()
        assert row.status == Status.FAILED
        assert row.error == "SMTPServerDisconnected"
        assert row.sent_at is None

    def test_a_failed_log_update_after_a_send_does_not_raise(self, catalogue):
        catalogue(probe())
        real_save = EmailLog.save

        def save(self, *args, **kwargs):
            if kwargs.get("update_fields"):
                raise OperationalError("db gone")
            return real_save(self, *args, **kwargs)

        with mock.patch.object(EmailLog, "save", save):
            deliveries = send_email("probe.notice", probe_object())

        assert deliveries[0].status is Status.SENT
        assert len(mail.outbox) == 1

    def test_a_row_that_cannot_be_written_never_stops_the_send(
        self, catalogue, caplog
    ):
        """An alert about a failing database must still go out, and a
        send must not wait on, or be retried because of, its own log."""
        catalogue(probe())

        with mock.patch.object(
            EmailLog, "save", side_effect=OperationalError("db gone")
        ):
            deliveries = send_email("probe.notice", probe_object())

        assert deliveries[0].status is Status.SENT
        assert len(mail.outbox) == 1
        assert "is not logged" in caplog.text
        assert not EmailLog.objects.exists()

    def test_a_failed_send_with_no_row_raises_the_transports_error(
        self, catalogue
    ):
        catalogue(probe())

        with (
            mock.patch.object(
                EmailLog, "save", side_effect=OperationalError("db gone")
            ),
            mock.patch(
                "emails.transport.EmailMultiAlternatives.send",
                side_effect=SMTPServerDisconnected("relay down"),
            ),
            pytest.raises(SMTPServerDisconnected),
        ):
            send_email("probe.notice", probe_object())

    def test_a_skip_that_cannot_be_logged_is_still_a_skip(self, catalogue):
        catalogue(probe(recipients=lambda obj: []))

        with mock.patch.object(
            EmailLog, "save", side_effect=OperationalError("db gone")
        ):
            [delivery] = send_email("probe.notice", probe_object())

        assert delivery.status is Status.SKIPPED_NO_RECIPIENT


class TestPrivacy:
    def test_no_column_of_any_row_holds_the_raw_address(self, catalogue):
        catalogue(probe(), probe("cart.abandoned", kind=Kind.MARKETING))
        send_email("probe.notice", probe_object())
        optout.opt_out(
            ADDRESS, "cart.abandoned", source=EmailOptOut.Source.LINK
        )
        send_email("cart.abandoned", probe_object())
        with (
            mock.patch(
                "emails.transport.EmailMultiAlternatives.send",
                side_effect=SMTPServerDisconnected(ADDRESS),
            ),
            pytest.raises(SMTPServerDisconnected),
        ):
            send_email("probe.notice", probe_object())

        rows = list(EmailLog.objects.values())
        assert len(rows) == 3
        for row in rows:
            for column, value in row.items():
                assert "customer@" not in str(value), column


class TestHelpers:
    @pytest.mark.parametrize(
        ("address", "masked"),
        [
            ("jane.doe@example.com", "j***@e***.com"),
            ("  A@b.co.uk ", "A***@b***.uk"),
            ("not-an-address", "***"),
            ("@example.com", "***"),
            ("user@localhost", "***"),
        ],
    )
    def test_mask_address(self, address, masked):
        assert mask_address(address) == masked

    def test_a_failure_is_described_by_class_and_code_only(self):
        smtp = SMTPAuthenticationError(535, b"bad creds for a@b.test")
        response = requests.Response()
        response.status_code = 503
        http = requests.HTTPError("503 for a@b.test", response=response)

        assert describe_failure(smtp) == "SMTPAuthenticationError smtp 535"
        assert describe_failure(http) == "HTTPError http 503"
        assert describe_failure(ValueError("a@b.test")) == "ValueError"

    @pytest.mark.parametrize(
        ("value", "valid"),
        [(90, True), (0, True), (-1, False), (True, False), ("90", False)],
    )
    def test_the_retention_setting_validator(self, value, valid):
        assert validate_retention_days_setting(value) is valid


def _aged(days: int) -> EmailLog:
    row = EmailLog.objects.create(type_key="probe.notice", status=Status.SENT)
    EmailLog.objects.filter(pk=row.pk).update(
        created_at=timezone.now() - timedelta(days=days)
    )
    return row


def _set_retention_days(days: int) -> None:
    Setting.objects.update_or_create(
        name="EMAIL_LOG_RETENTION_DAYS",
        defaults={"value_type": Setting.TYPE_INT, "value_int": days},
    )


class TestRetention:
    def test_the_default_period_is_ninety_days(self):
        old = _aged(91)
        kept = _aged(89)

        assert purge_expired_email_logs() == {"deleted": 1}

        assert list(EmailLog.objects.values_list("pk", flat=True)) == [kept.pk]
        assert not EmailLog.objects.filter(pk=old.pk).exists()

    def test_events_go_with_their_row(self):
        old = _aged(120)
        old.events.create(
            provider_event_id="msg_1",
            type="email.sent",
            occurred_at=timezone.now(),
        )

        purge_expired_email_logs()

        assert old.events.count() == 0

    def test_the_stores_own_period_applies(self):
        _set_retention_days(7)
        _aged(8)
        kept = _aged(6)

        purge_expired_email_logs()

        assert list(EmailLog.objects.values_list("pk", flat=True)) == [kept.pk]

    def test_zero_keeps_everything(self):
        _set_retention_days(0)
        _aged(4000)

        assert purge_expired_email_logs() == {"deleted": 0}
        assert EmailLog.objects.count() == 1

    def test_a_backlog_larger_than_a_batch_goes(self):
        for _ in range(5):
            _aged(200)

        with override_settings(RETENTION_PURGE_BATCH_SIZE=2):
            assert purge_expired_email_logs() == {"deleted": 5}
