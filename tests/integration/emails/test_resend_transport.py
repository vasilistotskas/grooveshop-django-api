"""The Resend HTTP transport: what is posted, with which key, and how a
failure is told apart for the calling task's retry policy.

HTTP is mocked at ``requests.post`` and nothing leaves the process.
"""

from __future__ import annotations

import base64
import json
from unittest import mock

import pytest
import requests
from django.core import mail

from core import celery_app
from core.retry import MAIL_TRANSIENT_ERRORS, is_permanent_failure
from emails.models import EmailLog, EmailProvider
from emails.recipients import Recipient
from emails.resend import API_URL, LOG_TAG, SCHEMA_TAG, ResendNotConfigured
from emails.send import Status, send_email
from emails.transport import Attachment
from emails.types import Audience, Owner
from tests.utils.emails import probe, probe_object

pytestmark = pytest.mark.django_db

PLATFORM_KEY = "re_platform_key"
STORE_KEY = "re_store_key"
RESEND_ID = "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"


@pytest.fixture(autouse=True)
def _resend(tenant, settings):
    settings.EMAIL_TRANSPORT = "resend"
    settings.RESEND_API_KEY = PLATFORM_KEY
    mail.outbox.clear()


def _response(status: int, body: dict | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body if body is not None else {}).encode()
    return response


@pytest.fixture
def post():
    """``requests.post``, answering 200 with Resend's id."""
    with mock.patch(
        "emails.resend.requests.post",
        return_value=_response(200, {"id": RESEND_ID}),
    ) as patched:
        yield patched


def _own_domain(tenant, *, key: str = STORE_KEY, verified: bool = True):
    tenant.from_email = "orders@brand.test"
    tenant.from_email_verified = verified
    tenant.resend_api_key = key
    tenant.save()
    return tenant


class TestRequest:
    def test_one_post_carries_the_message_the_key_and_the_idempotency_key(
        self, catalogue, post, settings
    ):
        catalogue(probe("order.shipped", list_id="shipping_notification"))

        deliveries = send_email("order.shipped", probe_object(marker="BODY"))

        assert [each.status for each in deliveries] == [Status.SENT]
        post.assert_called_once()
        [url] = post.call_args.args
        kwargs = post.call_args.kwargs
        log = EmailLog.objects.get()
        assert url == API_URL
        assert kwargs["headers"] == {
            "Authorization": f"Bearer {PLATFORM_KEY}",
            "Idempotency-Key": str(log.uuid),
        }
        assert kwargs["timeout"] == settings.EMAIL_SEND_TIMEOUT
        body = kwargs["json"]
        assert body["to"] == ["customer@example.test"]
        assert body["from"] == "Acme Gadgets <noreply@platform.test>"
        assert body["subject"] == "Your Order #7 Has Shipped"
        assert "<p>BODY</p>" in body["html"]
        assert "BODY" in body["text"]
        assert body["reply_to"] == ["owner@store.test"]
        assert (
            body["headers"]["List-ID"] == "<shipping_notification.shop.example>"
        )
        assert "List-Unsubscribe" in body["headers"]
        assert mail.outbox == []

    def test_the_log_row_records_the_provider_and_its_id(self, catalogue, post):
        catalogue(probe())

        send_email("probe.notice", probe_object())

        log = EmailLog.objects.get()
        assert log.status == Status.SENT
        assert log.provider == EmailProvider.RESEND
        assert log.provider_message_id == RESEND_ID

    def test_two_hidden_tags_carry_the_schema_and_the_log_uuid(
        self, catalogue, post, tenant
    ):
        catalogue(probe())

        send_email("probe.notice", probe_object())

        body = post.call_args.kwargs["json"]
        log = EmailLog.objects.get()
        tags = {tag["name"]: tag["value"] for tag in body["tags"]}
        assert tags == {
            SCHEMA_TAG: tenant.schema_name,
            LOG_TAG: str(log.uuid),
        }
        for tag in body["tags"]:
            assert tag["value"].replace("-", "").replace("_", "").isalnum()
            assert tag["value"].isascii()

    def test_no_tag_or_metadata_becomes_a_visible_header(self, catalogue, post):
        catalogue(probe())

        send_email("probe.notice", probe_object())

        headers = post.call_args.kwargs["json"].get("headers", {})
        assert not [name for name in headers if "tag" in name.lower()]
        assert str(EmailLog.objects.get().uuid) not in json.dumps(headers)

    def test_an_attachment_is_base64_with_its_name_and_type(
        self, catalogue, post
    ):
        catalogue(probe())

        send_email(
            "probe.notice",
            probe_object(),
            attachments=[
                Attachment("receipt.pdf", b"%PDF-1.7 data", "application/pdf")
            ],
        )

        [attachment] = post.call_args.kwargs["json"]["attachments"]
        assert attachment == {
            "filename": "receipt.pdf",
            "content": base64.b64encode(b"%PDF-1.7 data").decode(),
            "content_type": "application/pdf",
        }

    def test_a_message_without_reply_to_or_attachments_omits_them(
        self, catalogue, post
    ):
        catalogue(
            probe(owner=Owner.PLATFORM, audience=Audience.PLATFORM_OPERATOR)
        )

        send_email("probe.notice", probe_object())

        body = post.call_args.kwargs["json"]
        assert "reply_to" not in body
        assert "attachments" not in body

    def test_each_recipient_is_its_own_request_with_its_own_key(
        self, catalogue, post
    ):
        catalogue(
            probe(
                recipients=lambda obj: [
                    Recipient("a@example.test", "en"),
                    Recipient("b@example.test", "en"),
                ]
            )
        )

        post.side_effect = [
            _response(200, {"id": "id-a"}),
            _response(200, {"id": "id-b"}),
        ]

        send_email("probe.notice", probe_object())

        keys = [
            call.kwargs["headers"]["Idempotency-Key"]
            for call in post.call_args_list
        ]
        assert len(keys) == 2
        assert len(set(keys)) == 2
        assert {str(row.uuid) for row in EmailLog.objects.all()} == set(keys)


class TestWhichKey:
    def test_a_store_without_its_own_domain_uses_the_platforms_key(
        self, catalogue, post, tenant
    ):
        catalogue(probe())
        _own_domain(tenant, verified=False)

        send_email("probe.notice", probe_object())

        headers = post.call_args.kwargs["headers"]
        assert headers["Authorization"] == f"Bearer {PLATFORM_KEY}"

    def test_a_store_with_a_verified_own_domain_uses_its_own_key(
        self, catalogue, post, tenant
    ):
        catalogue(probe())
        _own_domain(tenant)

        send_email("probe.notice", probe_object())

        headers = post.call_args.kwargs["headers"]
        assert headers["Authorization"] == f"Bearer {STORE_KEY}"
        assert post.call_args.kwargs["json"]["from"] == (
            "Acme Gadgets <orders@brand.test>"
        )

    def test_a_verified_store_with_no_key_never_borrows_the_platforms(
        self, catalogue, post, tenant
    ):
        catalogue(probe())
        _own_domain(tenant, key="")

        with pytest.raises(ResendNotConfigured):
            send_email("probe.notice", probe_object())

        post.assert_not_called()
        log = EmailLog.objects.get()
        assert log.status == Status.FAILED
        assert log.error == "ResendNotConfigured"

    def test_a_platform_type_uses_the_platforms_key_even_from_a_verified_store(
        self, catalogue, post, tenant
    ):
        catalogue(
            probe(owner=Owner.PLATFORM, audience=Audience.PLATFORM_OPERATOR)
        )
        _own_domain(tenant)

        send_email("probe.notice", probe_object())

        headers = post.call_args.kwargs["headers"]
        assert headers["Authorization"] == f"Bearer {PLATFORM_KEY}"

    def test_without_a_platform_key_nothing_is_sent(
        self, catalogue, post, settings
    ):
        catalogue(probe())
        settings.RESEND_API_KEY = ""

        with pytest.raises(ResendNotConfigured):
            send_email("probe.notice", probe_object())

        post.assert_not_called()

    def test_a_missing_key_is_not_a_class_a_task_retries(self):
        assert not issubclass(ResendNotConfigured, MAIL_TRANSIENT_ERRORS)


class TestFailures:
    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_a_throttle_or_server_error_is_retried(
        self, catalogue, status, caplog
    ):
        catalogue(probe())
        with (
            mock.patch(
                "emails.resend.requests.post",
                return_value=_response(status, {"name": "rate_limit_exceeded"}),
            ),
            pytest.raises(requests.HTTPError) as raised,
        ):
            send_email("probe.notice", probe_object())

        assert isinstance(raised.value, MAIL_TRANSIENT_ERRORS)
        assert not is_permanent_failure(raised.value)

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_any_other_client_error_is_permanent(self, catalogue, status):
        catalogue(probe())
        with (
            mock.patch(
                "emails.resend.requests.post",
                return_value=_response(status, {"name": "validation_error"}),
            ),
            pytest.raises(requests.HTTPError) as raised,
        ):
            send_email("probe.notice", probe_object())

        assert is_permanent_failure(raised.value)

    @pytest.mark.parametrize(
        "error", [requests.Timeout("slow"), requests.ConnectionError("down")]
    )
    def test_a_timeout_or_a_dropped_connection_is_retried(
        self, catalogue, error
    ):
        catalogue(probe())
        with (
            mock.patch("emails.resend.requests.post", side_effect=error),
            pytest.raises(type(error)) as raised,
        ):
            send_email("probe.notice", probe_object())

        assert isinstance(raised.value, MAIL_TRANSIENT_ERRORS)
        assert not is_permanent_failure(raised.value)
        assert EmailLog.objects.get().status == Status.FAILED

    def test_the_failure_names_the_status_and_error_never_the_body_or_key(
        self, catalogue, caplog
    ):
        catalogue(probe())
        body = {
            "name": "validation_error",
            "message": "The `to` field customer@example.test is invalid",
        }
        with (
            mock.patch(
                "emails.resend.requests.post", return_value=_response(422, body)
            ),
            caplog.at_level("DEBUG"),
            pytest.raises(requests.HTTPError) as raised,
        ):
            send_email("probe.notice", probe_object())

        assert str(raised.value) == "Resend answered 422 validation_error"
        log = EmailLog.objects.get()
        assert log.status == Status.FAILED
        assert log.error == "HTTPError http 422"
        for text in (str(raised.value), log.error, caplog.text):
            assert "customer@example.test" not in text
            assert PLATFORM_KEY not in text

    def test_an_unreadable_error_body_still_gives_the_status(self, catalogue):
        catalogue(probe())
        response = _response(502)
        response._content = b"<html>Bad gateway</html>"
        with (
            mock.patch("emails.resend.requests.post", return_value=response),
            pytest.raises(requests.HTTPError) as raised,
        ):
            send_email("probe.notice", probe_object())

        assert str(raised.value) == "Resend answered 502"


class TestDemoSuppression:
    def test_a_demo_stores_own_mailbox_is_not_posted(self, catalogue, post):
        catalogue(probe())

        with mock.patch(
            "core.mail.demo_store_mailboxes",
            return_value=frozenset({"customer@example.test"}),
        ):
            send_email("probe.notice", probe_object())

        post.assert_not_called()

    def test_other_recipients_still_go(self, catalogue, post):
        catalogue(probe())

        with mock.patch(
            "core.mail.demo_store_mailboxes",
            return_value=frozenset({"someone.else@example.test"}),
        ):
            send_email("probe.notice", probe_object())

        post.assert_called_once()


class TestSmtpStaysTheDefault:
    def test_smtp_sends_through_django_and_posts_nothing(
        self, catalogue, post, settings
    ):
        settings.EMAIL_TRANSPORT = "smtp"
        catalogue(probe())

        send_email("probe.notice", probe_object())

        post.assert_not_called()
        [message] = mail.outbox
        assert message.to == ["customer@example.test"]
        log = EmailLog.objects.get()
        assert log.provider == EmailProvider.SMTP
        assert log.provider_message_id is None


class TestRetryAfterALostResponse:
    def test_the_retry_sends_the_same_idempotency_key_and_keeps_one_row(
        self, catalogue
    ):
        catalogue(probe())

        @celery_app.task(bind=True, max_retries=2, name="tests.resend_retry")
        def task(self):
            try:
                send_email("probe.notice", probe_object())
            except OSError as error:
                raise self.retry(exc=error, countdown=0) from error

        try:
            with mock.patch(
                "emails.resend.requests.post",
                side_effect=[
                    requests.Timeout("response lost"),
                    _response(200, {"id": RESEND_ID}),
                ],
            ) as post:
                task.apply(throw=False)
        finally:
            celery_app.tasks.pop("tests.resend_retry", None)

        keys = [
            call.kwargs["headers"]["Idempotency-Key"]
            for call in post.call_args_list
        ]
        assert len(keys) == 2
        assert keys[0] == keys[1]
        log = EmailLog.objects.get()
        assert keys[0] == str(log.uuid)
        assert log.status == Status.SENT
        assert log.provider_message_id == RESEND_ID
