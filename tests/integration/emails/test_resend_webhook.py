"""The Resend webhook: verification, the event's effect on its log row and
the suppression list, and what ``send_email`` does with a suppressed address.

HTTP is the real view through the test client; events are applied through
the real task body (``route``), so a signed payload goes the whole way.
"""

from __future__ import annotations

import json
import time
import uuid
from unittest import mock

import pytest
from django.core import mail
from rest_framework.test import APIClient

from emails import events, optout, svix
from emails.models import (
    EVERY_MARKETING_TYPE,
    EmailEvent,
    EmailLog,
    EmailOptOut,
    EmailSuppression,
)
from emails.send import Status, send_email
from emails.tasks import process_provider_event
from emails.types import Kind
from tests.utils.emails import probe, probe_object

pytestmark = pytest.mark.django_db

SECRET = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
OTHER_SECRET = "whsec_" + "QUJD" * 8
URL = "/resend/webhook/"
ADDRESS = "customer@example.test"
EMAIL_ID = "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"


@pytest.fixture(autouse=True)
def _setup(tenant, settings):
    settings.RESEND_WEBHOOK_SECRET = "platform-secret-is-not-this"
    tenant.resend_webhook_secret = SECRET
    tenant.save()
    mail.outbox.clear()


def _log(**fields) -> EmailLog:
    return EmailLog.objects.create(
        type_key="probe.notice",
        status=Status.SENT,
        provider="resend",
        provider_message_id=fields.pop("provider_message_id", EMAIL_ID),
        recipient_hash=fields.pop(
            "recipient_hash", optout.address_hash(ADDRESS)
        ),
        recipient_masked="c***@e***.test",
        **fields,
    )


def _event(log: EmailLog, kind: str, schema: str, **data) -> dict:
    return {
        "type": kind,
        "created_at": "2026-11-02T10:00:00.000Z",
        "data": {
            "email_id": log.provider_message_id,
            "to": [ADDRESS],
            "tags": {"schema": schema, "log": str(log.uuid)},
            **data,
        },
    }


def _post(
    payload, *, secret=SECRET, timestamp=None, message_id="msg_1", body=None
):
    raw = body if body is not None else json.dumps(payload).encode()
    timestamp = int(time.time()) if timestamp is None else timestamp
    return APIClient().post(
        URL,
        data=raw,
        content_type="application/json",
        HTTP_SVIX_ID=message_id,
        HTTP_SVIX_TIMESTAMP=str(timestamp),
        HTTP_SVIX_SIGNATURE=svix.sign(secret, message_id, timestamp, raw),
    )


class TestVerification:
    def test_a_signed_delivery_is_queued_and_answered_200(self, tenant):
        log = _log()
        payload = _event(log, "email.delivered", tenant.schema_name)

        with mock.patch.object(process_provider_event, "delay") as delay:
            response = _post(payload)

        assert response.status_code == 200
        delay.assert_called_once_with("msg_1", payload)

    def test_one_changed_byte_is_a_400_and_queues_nothing(self, tenant):
        payload = _event(_log(), "email.delivered", tenant.schema_name)
        raw = json.dumps(payload).encode()
        timestamp = int(time.time())
        signature = svix.sign(SECRET, "msg_1", timestamp, raw)

        with mock.patch.object(process_provider_event, "delay") as delay:
            response = APIClient().post(
                URL,
                data=raw.replace(b"delivered", b"delivereD"),
                content_type="application/json",
                HTTP_SVIX_ID="msg_1",
                HTTP_SVIX_TIMESTAMP=str(timestamp),
                HTTP_SVIX_SIGNATURE=signature,
            )

        assert response.status_code == 400
        delay.assert_not_called()

    def test_a_stale_timestamp_is_a_400(self, tenant, settings):
        payload = _event(_log(), "email.delivered", tenant.schema_name)

        with mock.patch.object(process_provider_event, "delay") as delay:
            response = _post(
                payload,
                timestamp=int(time.time())
                - settings.RESEND_WEBHOOK_TOLERANCE_SECONDS
                - 60,
            )

        assert response.status_code == 400
        delay.assert_not_called()

    def test_another_secret_is_a_400(self, tenant):
        payload = _event(_log(), "email.delivered", tenant.schema_name)

        with mock.patch.object(process_provider_event, "delay") as delay:
            response = _post(payload, secret=OTHER_SECRET)

        assert response.status_code == 400
        delay.assert_not_called()

    def test_a_store_host_never_accepts_the_platforms_secret(
        self, tenant, settings
    ):
        """No fallback between the two secrets."""
        settings.RESEND_WEBHOOK_SECRET = OTHER_SECRET
        payload = _event(_log(), "email.delivered", tenant.schema_name)

        with mock.patch.object(process_provider_event, "delay"):
            response = _post(payload, secret=OTHER_SECRET)

        assert response.status_code == 400

    def test_the_platform_host_uses_the_platform_secret(self, tenant, settings):
        settings.RESEND_WEBHOOK_SECRET = OTHER_SECRET
        payload = _event(_log(), "email.delivered", tenant.schema_name)

        with (
            mock.patch("emails.webhooks.in_tenant_schema", return_value=False),
            mock.patch.object(process_provider_event, "delay") as delay,
        ):
            ok = _post(payload, secret=OTHER_SECRET)
            store_secret = _post(payload, secret=SECRET)

        assert ok.status_code == 200
        assert store_secret.status_code == 400
        delay.assert_called_once()

    def test_a_host_without_a_secret_is_a_503(self, tenant):
        tenant.resend_webhook_secret = ""
        tenant.save()

        with mock.patch.object(process_provider_event, "delay") as delay:
            response = _post(_event(_log(), "email.delivered", "x"))

        assert response.status_code == 503
        delay.assert_not_called()

    def test_a_body_over_the_cap_is_a_413(self, tenant, settings):
        settings.RESEND_WEBHOOK_MAX_BODY_BYTES = 100
        payload = _event(_log(), "email.delivered", tenant.schema_name)

        assert _post(payload).status_code == 413

    @pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b'"text"'])
    def test_a_signed_body_that_is_not_a_json_object_is_a_400(
        self, tenant, body
    ):
        assert _post(None, body=body).status_code == 400

    def test_only_post_is_allowed(self):
        assert APIClient().get(URL).status_code == 405

    def test_a_signed_delivery_goes_the_whole_way(self, tenant):
        """The view queues, the task (run eagerly here) moves the row."""
        log = _log()
        payload = _event(log, "email.delivered", tenant.schema_name)

        with mock.patch.object(
            process_provider_event,
            "delay",
            side_effect=lambda *a: events.route(*a),
        ):
            assert _post(payload).status_code == 200

        log.refresh_from_db()
        assert log.status == Status.DELIVERED


class TestStatusMapping:
    @pytest.mark.parametrize(
        ("kind", "data", "status"),
        [
            ("email.sent", {}, Status.SENT),
            ("email.delivered", {}, Status.DELIVERED),
            ("email.delivery_delayed", {}, Status.DELAYED),
            (
                "email.bounced",
                {"bounce": {"type": "Permanent", "subType": "General"}},
                Status.BOUNCED,
            ),
            (
                "email.bounced",
                {"bounce": {"type": "Transient", "subType": "MailboxFull"}},
                Status.DELAYED,
            ),
            ("email.complained", {}, Status.COMPLAINED),
            (
                "email.failed",
                {"failed": {"reason": "reached_daily_quota"}},
                Status.FAILED,
            ),
            (
                "email.suppressed",
                {"suppressed": {"type": "OnAccountSuppressionList"}},
                Status.PROVIDER_SUPPRESSED,
            ),
        ],
    )
    def test_each_subscribed_event(self, kind, data, status):
        log = _log()

        assert events.process("msg_x", _event(log, kind, "s", **data))

        log.refresh_from_db()
        assert log.status == status
        [event] = log.events.all()
        assert event.type == kind
        assert event.provider_event_id == "msg_x"
        assert log.last_event_at is not None

    def test_an_event_nothing_acts_on_is_still_recorded(self):
        log = _log()

        events.process("msg_x", _event(log, "email.opened", "s"))

        log.refresh_from_db()
        assert log.status == Status.SENT
        assert log.events.count() == 1

    def test_the_detail_holds_tokens_never_an_address_or_a_message(self):
        log = _log()
        payload = _event(
            log,
            "email.bounced",
            "s",
            bounce={
                "type": "Permanent",
                "subType": "General",
                "message": f"550 {ADDRESS} does not exist",
                "diagnosticCode": [f"smtp; 550 {ADDRESS}"],
            },
        )

        events.process("msg_x", payload)

        [event] = log.events.all()
        assert event.detail == {"type": "Permanent", "subType": "General"}
        assert ADDRESS not in json.dumps(event.detail)

    def test_a_failure_keeps_only_the_reason_token(self):
        log = _log()

        events.process(
            "msg_x",
            _event(
                log,
                "email.failed",
                "s",
                failed={"reason": f"quota hit for {ADDRESS}"},
            ),
        )

        log.refresh_from_db()
        assert log.status == Status.FAILED
        assert log.error == ""

    def test_the_status_only_moves_forward(self):
        log = _log()
        events.process("m1", _event(log, "email.delivered", "s"))

        events.process("m2", _event(log, "email.delivery_delayed", "s"))
        log.refresh_from_db()
        assert log.status == Status.DELIVERED

        events.process("m3", _event(log, "email.complained", "s"))
        log.refresh_from_db()
        assert log.status == Status.COMPLAINED

        events.process("m4", _event(log, "email.delivered", "s"))
        log.refresh_from_db()
        assert log.status == Status.COMPLAINED
        assert log.events.count() == 4


class TestDedupe:
    def test_the_same_svix_id_is_processed_once(self):
        log = _log()
        payload = _event(
            log,
            "email.bounced",
            "s",
            bounce={"type": "Permanent", "subType": "General"},
        )

        assert events.process("msg_same", payload) is True
        assert events.process("msg_same", payload) is False

        assert EmailEvent.objects.count() == 1
        assert EmailSuppression.objects.count() == 1


class TestMatching:
    def test_another_rows_provider_id_updates_nothing(self):
        log = _log()
        payload = _event(log, "email.delivered", "s")
        payload["data"]["email_id"] = "someone-elses-id"

        with pytest.raises(events.EmailLogNotFound):
            events.process("msg_x", payload)

        log.refresh_from_db()
        assert log.status == Status.SENT
        assert not EmailEvent.objects.exists()

    def test_another_rows_uuid_updates_nothing(self):
        log = _log()
        payload = _event(log, "email.delivered", "s")
        payload["data"]["tags"]["log"] = str(uuid.uuid4())

        with pytest.raises(events.EmailLogNotFound):
            events.process("msg_x", payload)

    @pytest.mark.parametrize(
        "tags", [{}, {"log": "not-a-uuid"}, {"schema": "s"}]
    )
    def test_missing_or_malformed_tags_find_nothing(self, tags):
        log = _log()
        payload = _event(log, "email.delivered", "s")
        payload["data"]["tags"] = tags

        with pytest.raises(events.EmailLogNotFound):
            events.process("msg_x", payload)

    def test_a_row_that_is_not_there_yet_is_a_retry_not_a_loss(self):
        """A fast event can beat the send's write of the provider id."""
        log = _log()
        payload = _event(log, "email.delivered", "s")
        payload["data"]["email_id"] = "not-written-yet"

        with pytest.raises(events.EmailLogNotFound):
            events.process("msg_x", payload)

        assert events.EmailLogNotFound in process_provider_event.autoretry_for


class TestSuppression:
    def _bounce(self, log, kind="Permanent"):
        return _event(
            log,
            "email.bounced",
            "s",
            bounce={"type": kind, "subType": "General"},
        )

    def test_a_hard_bounce_suppresses_every_email(self):
        log = _log()

        events.process("m1", self._bounce(log))

        row = EmailSuppression.objects.get()
        assert row.address_hash == optout.address_hash(ADDRESS)
        assert row.scope == EmailSuppression.Scope.ALL
        assert row.reason == EmailSuppression.Reason.HARD_BOUNCE
        assert row.recipient_masked == "c***@e***.test"
        assert row.source_log == log

    def test_a_soft_bounce_suppresses_nothing(self):
        events.process("m1", self._bounce(_log(), "Transient"))

        assert not EmailSuppression.objects.exists()

    def test_a_complaint_suppresses_marketing_only_and_opts_out(self):
        log = _log()

        events.process("m1", _event(log, "email.complained", "s"))

        row = EmailSuppression.objects.get()
        assert row.scope == EmailSuppression.Scope.MARKETING
        assert row.reason == EmailSuppression.Reason.COMPLAINT
        opt_out = EmailOptOut.objects.get()
        assert opt_out.type_key == EVERY_MARKETING_TYPE
        assert opt_out.source == EmailOptOut.Source.COMPLAINT
        assert opt_out.address_hash == optout.address_hash(ADDRESS)

    def test_a_bounce_widens_a_complaint_but_a_complaint_never_narrows(self):
        log = _log()
        events.process("m1", _event(log, "email.complained", "s"))
        events.process("m2", self._bounce(log))

        row = EmailSuppression.objects.get()
        assert row.scope == EmailSuppression.Scope.ALL
        assert row.reason == EmailSuppression.Reason.HARD_BOUNCE

        events.process("m3", _event(log, "email.complained", "s"))
        row.refresh_from_db()
        assert row.scope == EmailSuppression.Scope.ALL

    def test_a_provider_suppression_is_mirrored_for_every_email(self):
        events.process(
            "m1",
            _event(
                _log(),
                "email.suppressed",
                "s",
                suppressed={"type": "OnAccountSuppressionList"},
            ),
        )

        row = EmailSuppression.objects.get()
        assert row.scope == EmailSuppression.Scope.ALL
        assert row.reason == EmailSuppression.Reason.PROVIDER

    def test_a_row_without_a_recipient_suppresses_nobody(self):
        log = _log(recipient_hash="")

        events.process("m1", self._bounce(log))

        assert not EmailSuppression.objects.exists()


class TestSendEmailHonoursTheList:
    def _suppress(self, scope):
        EmailSuppression.objects.create(
            address_hash=optout.address_hash(ADDRESS),
            recipient_masked="c***@e***.test",
            reason=EmailSuppression.Reason.HARD_BOUNCE,
            scope=scope,
        )

    def test_an_all_suppression_skips_a_transactional_mail_with_a_row(
        self, catalogue, caplog
    ):
        catalogue(probe("order.shipped"))
        self._suppress(EmailSuppression.Scope.ALL)

        with caplog.at_level("WARNING", logger="emails.send"):
            [delivery] = send_email("order.shipped", probe_object())

        assert delivery.status is Status.SKIPPED_SUPPRESSED
        assert mail.outbox == []
        row = EmailLog.objects.get(type_key="order.shipped")
        assert row.status == Status.SKIPPED_SUPPRESSED
        assert row.recipient_hash == optout.address_hash(ADDRESS)
        assert "order.shipped" in caplog.text
        assert ADDRESS not in caplog.text

    def test_a_marketing_scope_lets_a_transactional_mail_through(
        self, catalogue
    ):
        catalogue(probe("order.shipped"))
        self._suppress(EmailSuppression.Scope.MARKETING)

        [delivery] = send_email("order.shipped", probe_object())

        assert delivery.status is Status.SENT
        assert len(mail.outbox) == 1

    def test_a_marketing_scope_blocks_a_marketing_mail(self, catalogue):
        catalogue(probe("cart.abandoned", kind=Kind.MARKETING))
        self._suppress(EmailSuppression.Scope.MARKETING)

        [delivery] = send_email("cart.abandoned", probe_object())

        assert delivery.status is Status.SKIPPED_SUPPRESSED
        assert mail.outbox == []

    def test_another_address_is_not_affected(self, catalogue):
        catalogue(probe())
        self._suppress(EmailSuppression.Scope.ALL)

        [delivery] = send_email(
            "probe.notice",
            probe_object(email="someone.else@example.test"),
        )

        assert delivery.status is Status.SENT
