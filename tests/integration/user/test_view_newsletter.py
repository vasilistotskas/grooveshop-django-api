"""The anonymous newsletter form, its double opt-in and the consent record.

``POST /api/v1/user/subscription/newsletter`` subscribes an address to
the store's default newsletter topic. It must never reveal whether the
address is known (always 202), never create an ACTIVE row (the emailed
link does that), and keep the consent evidence it was given.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from allauth.account.models import EmailAddress
from django.core import mail, signing
from django.core.cache.backends.locmem import LocMemCache
from django.db import IntegrityError, connection, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core.api.throttling import NewsletterSubscribeThrottle
from user.factories.account import UserAccountFactory
from user.models.subscription import (
    CONFIRMATION_TTL,
    SubscriptionTopic,
    UserSubscription,
)
from user.serializers.subscription import SubscriptionTopicWriteSerializer
from user.utils.subscription import UNSUBSCRIBE_SALT, generate_unsubscribe_link

pytestmark = pytest.mark.django_db

EDGE_SECRET = "edge-shared-secret"
CONSENT = "I want the store's newsletter. I can unsubscribe at any time."
URL = "/api/v1/user/subscription/newsletter"

Status = UserSubscription.SubscriptionStatus


@pytest.fixture
def topic():
    return SubscriptionTopic.objects.create(
        slug="newsletter",
        name="Newsletter",
        category=SubscriptionTopic.TopicCategory.NEWSLETTER,
        is_active=True,
        is_default=True,
    )


@pytest.fixture(autouse=True)
def _edge_secret(settings):
    # Requests below that carry this secret provably came through the
    # edge, so their CF-Connecting-IP is believed (core.client_ip).
    settings.ORIGIN_VERIFY_SECRET = EDGE_SECRET


@pytest.fixture
def client():
    return APIClient()


def _post(client, email="guest@example.com", **overrides):
    body = {"email": email, "consent": True, "consentText": CONSENT}
    body.update(overrides)
    return client.post(
        URL,
        body,
        format="json",
        headers={
            "x-origin-verify": EDGE_SECRET,
            "cf-connecting-ip": "203.0.113.9",
            "user-agent": "Mozilla/5.0 (Test)",
            "x-language": "en",
        },
    )


class TestConstraints:
    def test_one_guest_row_per_address_and_topic_case_insensitive(self, topic):
        UserSubscription.objects.create(topic=topic, email="a@example.com")
        with pytest.raises(IntegrityError), transaction.atomic():
            UserSubscription.objects.create(topic=topic, email="A@Example.com")

    def test_one_account_row_per_topic(self, topic):
        user = UserAccountFactory()
        UserSubscription.objects.create(user=user, topic=topic)
        with pytest.raises(IntegrityError), transaction.atomic():
            UserSubscription.objects.create(user=user, topic=topic)

    def test_a_row_needs_a_recipient(self, topic):
        with pytest.raises(IntegrityError), transaction.atomic():
            UserSubscription.objects.create(topic=topic)

    def test_account_row_and_guest_row_for_one_address_coexist(self, topic):
        user = UserAccountFactory(email="same@example.com")
        UserSubscription.objects.create(user=user, topic=topic)
        UserSubscription.objects.create(topic=topic, email="same@example.com")

    def test_one_default_newsletter_topic(self, topic):
        with pytest.raises(IntegrityError), transaction.atomic():
            SubscriptionTopic.objects.create(
                slug="second",
                name="Second",
                category=SubscriptionTopic.TopicCategory.NEWSLETTER,
                is_active=True,
                is_default=True,
            )
        # Non-default and inactive newsletter topics are unrestricted.
        SubscriptionTopic.objects.create(
            slug="plain",
            name="Plain",
            category=SubscriptionTopic.TopicCategory.NEWSLETTER,
        )
        SubscriptionTopic.objects.create(
            slug="retired",
            name="Retired",
            category=SubscriptionTopic.TopicCategory.NEWSLETTER,
            is_active=False,
            is_default=True,
        )

    def test_write_serializer_refuses_a_second_default(self, topic):
        data = {
            "translations": {"el": {"name": "Δεύτερο"}},
            "slug": "second",
            "category": "NEWSLETTER",
            "is_active": True,
            "is_default": True,
        }
        serializer = SubscriptionTopicWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "is_default" in serializer.errors

        # ...but still accepts a non-default newsletter topic.
        data["is_default"] = False
        assert SubscriptionTopicWriteSerializer(data=data).is_valid()


class TestAvailability:
    def test_available_with_a_default_newsletter_topic(self, client, topic):
        response = client.get(URL)
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"available": True}

    def test_unavailable_without_one(self, client):
        SubscriptionTopic.objects.create(
            slug="not-default",
            name="Not default",
            category=SubscriptionTopic.TopicCategory.NEWSLETTER,
        )
        response = client.get(URL)
        assert response.json() == {"available": False}


class TestSubscribe:
    def test_new_address_gets_a_pending_row_and_one_email(
        self, client, topic, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            response = _post(client)

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert "detail" in response.json()
        row = UserSubscription.objects.get(topic=topic)
        assert row.user is None
        assert row.email == "guest@example.com"
        assert row.status == Status.PENDING
        assert row.source == UserSubscription.Source.NEWSLETTER_FORM
        assert len(row.confirmation_token) == 64
        assert row.confirmation_sent_at is not None
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["guest@example.com"]
        assert row.confirmation_token in mail.outbox[0].body

    def test_consent_record_is_stored(self, client, topic):
        _post(client)

        row = UserSubscription.objects.get(topic=topic)
        assert row.consent_text == CONSENT
        assert row.consent_ip == "203.0.113.9"
        assert row.consent_user_agent == "Mozilla/5.0 (Test)"
        assert row.language == "en"

    def test_unproven_ip_is_not_recorded(self, client, topic):
        client.post(
            URL,
            {"email": "x@example.com", "consent": True, "consentText": CONSENT},
            format="json",
            headers={"cf-connecting-ip": "203.0.113.9"},
        )
        row = UserSubscription.objects.get(topic=topic)
        # A forgeable header without proof of edge is not evidence.
        assert row.consent_ip is None

    def test_active_address_gets_the_already_subscribed_notice_only(
        self, client, topic, django_capture_on_commit_callbacks
    ):
        row = UserSubscription.objects.create(
            topic=topic,
            email="guest@example.com",
            status=Status.ACTIVE,
            consent_text="original",
            language="en",
        )

        with django_capture_on_commit_callbacks(execute=True):
            response = _post(client)

        assert response.status_code == status.HTTP_202_ACCEPTED
        row.refresh_from_db()
        # Nothing about the subscription itself changed.
        assert row.status == Status.ACTIVE
        assert row.consent_text == "original"
        assert row.confirmation_token == ""
        assert row.already_subscribed_notice_sent_at is not None
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == ["guest@example.com"]
        assert "already subscribed" in message.subject
        # A guest has no login: the link is the topic's unsubscribe one.
        assert "/api/v1/user/unsubscribe/" in message.body
        assert "/account/subscriptions" not in message.body

    def test_second_submission_inside_the_cooldown_sends_nothing(
        self, client, topic, django_capture_on_commit_callbacks
    ):
        UserSubscription.objects.create(
            topic=topic, email="guest@example.com", status=Status.ACTIVE
        )
        with django_capture_on_commit_callbacks(execute=True):
            _post(client)
        with django_capture_on_commit_callbacks(execute=True):
            response = _post(client, email="GUEST@example.com")

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert len(mail.outbox) == 1

    def test_submission_after_the_cooldown_sends_another_notice(
        self, client, topic, django_capture_on_commit_callbacks
    ):
        row = UserSubscription.objects.create(
            topic=topic, email="guest@example.com", status=Status.ACTIVE
        )
        with django_capture_on_commit_callbacks(execute=True):
            _post(client)
        UserSubscription.objects.filter(pk=row.pk).update(
            already_subscribed_notice_sent_at=timezone.now()
            - timedelta(minutes=11)
        )

        with django_capture_on_commit_callbacks(execute=True):
            _post(client)

        assert len(mail.outbox) == 2

    def test_the_notice_is_off_with_the_newsletter_feature(
        self, client, topic, django_capture_on_commit_callbacks
    ):
        UserSubscription.objects.create(
            topic=topic, email="guest@example.com", status=Status.ACTIVE
        )

        with (
            patch("emails.gates.feature_on", return_value=False),
            django_capture_on_commit_callbacks(execute=True),
        ):
            response = _post(client)

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert mail.outbox == []

    def test_an_account_row_is_mailed_the_manage_link_not_an_unsubscribe_link(
        self, client, topic, django_capture_on_commit_callbacks
    ):
        """An account row, reached by an anonymous submission of an
        address the account owns: the task mails it."""
        user = UserAccountFactory(email="owner@example.com")
        EmailAddress.objects.update_or_create(
            user=user,
            email="owner@example.com",
            defaults={"verified": True, "primary": True},
        )
        UserSubscription.objects.create(
            user=user, topic=topic, status=Status.ACTIVE
        )

        with django_capture_on_commit_callbacks(execute=True):
            response = _post(client, email="owner@example.com")

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["owner@example.com"]
        assert "/account/subscriptions" in mail.outbox[0].body
        assert "/api/v1/user/unsubscribe/" not in mail.outbox[0].body

    def test_pending_row_inside_the_cooldown_sends_no_notice(
        self, client, topic, django_capture_on_commit_callbacks
    ):
        """A confirmation link was just sent: it is the answer."""
        with django_capture_on_commit_callbacks(execute=True):
            _post(client)
        with django_capture_on_commit_callbacks(execute=True):
            _post(client)

        assert len(mail.outbox) == 1
        assert "already subscribed" not in mail.outbox[0].subject

    def test_resubmission_inside_the_cooldown_resends_nothing(
        self, client, topic, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            _post(client)
        token = UserSubscription.objects.get(topic=topic).confirmation_token

        with django_capture_on_commit_callbacks(execute=True):
            response = _post(client, email="GUEST@example.com")

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert UserSubscription.objects.filter(topic=topic).count() == 1
        assert (
            UserSubscription.objects.get(topic=topic).confirmation_token
            == token
        )
        assert len(mail.outbox) == 1

    def test_resubmission_after_the_cooldown_sends_a_fresh_link(
        self, client, topic, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            _post(client)
        row = UserSubscription.objects.get(topic=topic)
        old_token = row.confirmation_token
        UserSubscription.objects.filter(pk=row.pk).update(
            confirmation_sent_at=timezone.now() - timedelta(minutes=11)
        )

        with django_capture_on_commit_callbacks(execute=True):
            _post(client)

        row.refresh_from_db()
        assert row.status == Status.PENDING
        assert row.confirmation_token != old_token
        assert len(mail.outbox) == 2

    @pytest.mark.parametrize("state", [Status.UNSUBSCRIBED, Status.BOUNCED])
    def test_unsubscribed_or_bounced_is_rearmed_with_fresh_consent(
        self, client, topic, state, django_capture_on_commit_callbacks
    ):
        row = UserSubscription.objects.create(
            topic=topic,
            email="guest@example.com",
            status=state,
            consent_text="old sentence",
            unsubscribed_at=timezone.now(),
        )

        with django_capture_on_commit_callbacks(execute=True):
            response = _post(client)

        assert response.status_code == status.HTTP_202_ACCEPTED
        row.refresh_from_db()
        assert row.status == Status.PENDING
        assert row.consent_text == CONSENT
        assert row.unsubscribed_at is None
        assert len(row.confirmation_token) == 64
        assert len(mail.outbox) == 1

    def test_verified_account_address_subscribes_the_account(
        self, client, topic, django_capture_on_commit_callbacks
    ):
        user = UserAccountFactory(email="owner@example.com")
        EmailAddress.objects.update_or_create(
            user=user,
            email="owner@example.com",
            defaults={"verified": True, "primary": True},
        )

        with django_capture_on_commit_callbacks(execute=True):
            response = _post(client, email="Owner@example.com")

        assert response.status_code == status.HTTP_202_ACCEPTED
        row = UserSubscription.objects.get(topic=topic)
        assert row.user == user
        # Still double opt-in.
        assert row.status == Status.PENDING
        assert mail.outbox[0].to == ["owner@example.com"]

    def test_unverified_account_address_stays_a_guest_row(self, client, topic):
        user = UserAccountFactory(email="claimed@example.com")
        EmailAddress.objects.filter(user=user).delete()
        EmailAddress.objects.create(
            user=user, email="claimed@example.com", verified=False
        )

        _post(client, email="claimed@example.com")

        row = UserSubscription.objects.get(topic=topic)
        assert row.user is None

    @pytest.mark.parametrize(
        "overrides",
        [{"consent": False}, {"consent": None}, {"consentText": ""}],
    )
    def test_consent_is_required(self, client, topic, overrides):
        response = _post(client, **overrides)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not UserSubscription.objects.exists()

    def test_invalid_email_is_rejected(self, client, topic):
        response = _post(client, email="not-an-email")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_no_default_newsletter_topic_is_404(self, client):
        response = _post(client)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "detail" in response.json()


@pytest.fixture
def throttled(monkeypatch):
    """A real rate and a private cache — see
    tests/integration/b2b/test_profile_submit_throttle.py for why both."""
    monkeypatch.setattr(
        NewsletterSubscribeThrottle, "rate", "2/minute", raising=False
    )
    monkeypatch.setattr(
        NewsletterSubscribeThrottle,
        "cache",
        LocMemCache(f"newsletter-throttle-{uuid4()}", {}),
        raising=False,
    )


class TestThrottle:
    def _post_from(self, client, ip):
        return client.post(
            URL,
            {
                "email": f"{uuid4().hex}@example.com",
                "consent": True,
                "consentText": CONSENT,
            },
            format="json",
            headers={"x-origin-verify": EDGE_SECRET, "cf-connecting-ip": ip},
        )

    def test_budget_is_per_client_ip(self, client, topic, throttled):
        assert self._post_from(client, "203.0.113.9").status_code == 202
        assert self._post_from(client, "203.0.113.9").status_code == 202
        assert self._post_from(client, "203.0.113.9").status_code == 429
        # Another visitor behind the same edge has a budget of their own.
        assert self._post_from(client, "198.51.100.7").status_code == 202

    def test_availability_read_spends_no_budget(self, client, topic, throttled):
        for _ in range(5):
            assert client.get(URL).status_code == 200
        assert self._post_from(client, "203.0.113.9").status_code == 202


def _pending(topic, **kwargs):
    row = UserSubscription(topic=topic, email="guest@example.com", **kwargs)
    row.arm_confirmation()
    row.save()
    return row


def _confirm_url(token):
    return reverse("user-subscription-confirm-by-token", args=[token])


class TestConfirm:
    def test_post_confirms_and_records_when_and_where(self, client, topic):
        row = _pending(topic)

        response = client.post(
            _confirm_url(row.confirmation_token),
            headers={
                "x-origin-verify": EDGE_SECRET,
                "cf-connecting-ip": "198.51.100.7",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["status"] == "confirmed"
        row.refresh_from_db()
        assert row.status == Status.ACTIVE
        assert row.confirmation_token == ""
        assert row.confirmed_at is not None
        assert row.confirmed_ip == "198.51.100.7"

    def test_get_does_not_confirm(self, client, topic):
        """A mail scanner prefetching the link must not subscribe anyone."""
        row = _pending(topic)

        response = client.get(_confirm_url(row.confirmation_token))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        row.refresh_from_db()
        assert row.status == Status.PENDING

    def test_expired_link_is_gone(self, client, topic):
        row = _pending(topic)
        UserSubscription.objects.filter(pk=row.pk).update(
            confirmation_sent_at=timezone.now()
            - CONFIRMATION_TTL
            - timedelta(minutes=1)
        )

        response = client.post(_confirm_url(row.confirmation_token))

        assert response.status_code == status.HTTP_410_GONE
        assert "detail" in response.json()
        row.refresh_from_db()
        assert row.status == Status.PENDING

    def test_unknown_token_is_400(self, client):
        response = client.post(_confirm_url("x" * 64))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "detail" in response.json()

    def test_token_of_an_active_row_does_not_reconfirm(self, client, topic):
        row = _pending(topic)
        token = row.confirmation_token
        assert row.confirm(token, ip=None) == "confirmed"

        response = client.post(_confirm_url(token))
        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestGuestUnsubscribe:
    @override_settings(API_BASE_URL="https://api.test-site.com")
    def test_sid_link_unsubscribes_the_guest_row(self, client, topic):
        row = UserSubscription.objects.create(
            topic=topic, email="guest@example.com", status=Status.ACTIVE
        )
        path = generate_unsubscribe_link(row).split(
            "https://api.test-site.com"
        )[1]

        response = client.get(path)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["userEmail"] == "guest@example.com"
        row.refresh_from_db()
        assert row.status == Status.UNSUBSCRIBED

    def test_one_click_post_with_sid(self, client, topic):
        row = UserSubscription.objects.create(
            topic=topic, email="guest@example.com", status=Status.ACTIVE
        )
        token = signing.dumps(
            {"schema": connection.schema_name, "sid": str(row.uuid)},
            salt=UNSUBSCRIBE_SALT,
        )

        response = client.post(
            reverse("user-unsubscribe-topic", args=[token, topic.slug])
        )

        assert response.status_code == status.HTTP_200_OK
        row.refresh_from_db()
        assert row.status == Status.UNSUBSCRIBED

    def test_sid_minted_for_another_tenant_is_rejected(self, client, topic):
        """Schema-scoped like the account token: a valid signature from
        another store's domain unsubscribes nothing here."""
        row = UserSubscription.objects.create(
            topic=topic, email="guest@example.com", status=Status.ACTIVE
        )
        token = signing.dumps(
            {"schema": "tenant_b", "sid": str(row.uuid)},
            salt=UNSUBSCRIBE_SALT,
        )

        get = client.get(
            reverse("user-unsubscribe-topic", args=[token, topic.slug])
        )
        post = client.post(
            reverse("user-unsubscribe-topic", args=[token, topic.slug])
        )

        assert get.status_code == status.HTTP_400_BAD_REQUEST
        # RFC 8058: one-click stays silent, but changes nothing.
        assert post.status_code == status.HTTP_200_OK
        row.refresh_from_db()
        assert row.status == Status.ACTIVE


class TestRequestPathIsAddressIndependent:
    """The request must cost the same whatever the address is: new,
    already confirmed, inside the resend cooldown, or an account's. It
    writes nothing and enqueues exactly one task; the address-dependent
    work runs in the task. A branch that skipped the save and the broker
    publish answered measurably faster, which told a prober which
    addresses subscribe here."""

    def _request_side(self, client, email):
        from unittest.mock import patch

        from django.test.utils import CaptureQueriesContext

        with (
            patch(
                "user.tasks.subscribe_to_newsletter_task.apply_async"
            ) as enqueue,
            CaptureQueriesContext(connection) as queries,
        ):
            response = _post(client, email=email)
        writes = [
            q["sql"]
            for q in queries.captured_queries
            if q["sql"]
            .lstrip()
            .upper()
            .startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        return response, enqueue, len(queries.captured_queries), writes

    def test_every_branch_does_the_same_request_side_work(self, client, topic):
        UserSubscription.objects.create(
            topic=topic, email="active@example.com", status=Status.ACTIVE
        )
        cooling = UserSubscription(topic=topic, email="cooling@example.com")
        cooling.arm_confirmation()
        cooling.save()
        owner = UserAccountFactory(email="owner@example.com")
        EmailAddress.objects.update_or_create(
            user=owner,
            email="owner@example.com",
            defaults={"verified": True, "primary": True},
        )

        observed = []
        for email in (
            "new@example.com",
            "active@example.com",
            "cooling@example.com",
            "owner@example.com",
        ):
            response, enqueue, query_count, writes = self._request_side(
                client, email
            )
            assert response.status_code == status.HTTP_202_ACCEPTED
            assert writes == [], email
            enqueue.assert_called_once()
            kwargs = enqueue.call_args.kwargs["kwargs"]
            assert kwargs["email"] == email
            assert set(kwargs) == {
                "topic_id",
                "email",
                "consent_text",
                "consent_ip",
                "consent_user_agent",
                "language",
                "consented_at",
            }
            # The tenant schema travels with the task.
            assert "_schema_name" in enqueue.call_args.kwargs["headers"]
            observed.append((response.json(), query_count))

        # Same answer, same number of queries, for every address.
        assert len({str(body) for body, _ in observed}) == 1
        assert len({count for _, count in observed}) == 1

    def test_the_task_records_the_consent_time_of_the_request(
        self, client, topic
    ):
        before = timezone.now()
        _post(client)
        row = UserSubscription.objects.get(topic=topic)
        assert row.consented_at is not None
        assert before <= row.consented_at <= timezone.now()

    def test_task_skips_a_topic_that_stopped_being_the_default(self, topic):
        from user.tasks import subscribe_to_newsletter_task

        SubscriptionTopic.objects.filter(pk=topic.pk).update(is_default=False)
        result = subscribe_to_newsletter_task.apply(
            kwargs={
                "topic_id": topic.pk,
                "email": "late@example.com",
                "consent_text": CONSENT,
                "consent_ip": None,
                "consent_user_agent": "",
                "language": "el",
                "consented_at": timezone.now().isoformat(),
            }
        ).result
        assert result == {"status": "skipped", "reason": "topic_unavailable"}
        assert not UserSubscription.objects.exists()


def _verified_user(email="owner@example.com"):
    user = UserAccountFactory(email=email)
    EmailAddress.objects.update_or_create(
        user=user, email=email, defaults={"verified": True, "primary": True}
    )
    return user


class TestAnswerStatus:
    """The answer says ``pending_confirmation`` to every visitor, and
    ``already_subscribed`` only to an account about its OWN data."""

    def test_an_anonymous_answer_is_pending_confirmation_for_every_address(
        self, client, topic
    ):
        UserSubscription.objects.create(
            topic=topic, email="active@example.com", status=Status.ACTIVE
        )
        owner = _verified_user("owner@example.com")
        UserSubscription.objects.create(
            user=owner, topic=topic, status=Status.ACTIVE
        )

        bodies = [
            _post(client, email=email).json()
            for email in (
                "new@example.com",
                "active@example.com",
                "owner@example.com",
            )
        ]

        assert len({str(body) for body in bodies}) == 1
        assert set(bodies[0]) == {"detail", "status"}
        assert bodies[0]["status"] == "pending_confirmation"

    def test_a_signed_in_owner_of_an_active_address_is_told_so(
        self, client, topic, django_capture_on_commit_callbacks
    ):
        user = _verified_user("owner@example.com")
        UserSubscription.objects.create(
            user=user, topic=topic, status=Status.ACTIVE
        )
        client.force_authenticate(user=user)

        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            response = _post(client, email="Owner@Example.com")

        assert response.status_code == status.HTTP_202_ACCEPTED
        body = response.json()
        assert body["status"] == "already_subscribed"
        assert body["detail"]
        # The data is theirs and the answer says it: no email, no task.
        assert callbacks == []
        assert mail.outbox == []

    def test_the_owner_of_a_second_verified_address_is_told_so_too(
        self, client, topic
    ):
        user = _verified_user("owner@example.com")
        EmailAddress.objects.create(
            user=user, email="second@example.com", verified=True
        )
        UserSubscription.objects.create(
            user=user, topic=topic, status=Status.ACTIVE
        )
        client.force_authenticate(user=user)

        response = _post(client, email="second@example.com")

        assert response.json()["status"] == "already_subscribed"

    def test_a_signed_in_user_submitting_another_address_gets_the_generic_answer(
        self, client, topic, django_capture_on_commit_callbacks
    ):
        user = _verified_user("owner@example.com")
        UserSubscription.objects.create(
            user=user, topic=topic, status=Status.ACTIVE
        )
        UserSubscription.objects.create(
            topic=topic, email="other@example.com", status=Status.ACTIVE
        )
        client.force_authenticate(user=user)

        with django_capture_on_commit_callbacks(execute=True):
            response = _post(client, email="other@example.com")

        assert response.json()["status"] == "pending_confirmation"
        # The address is someone else's: its owner gets the notice.
        assert [m.to for m in mail.outbox] == [["other@example.com"]]

    def test_an_unverified_own_address_gets_the_generic_answer(
        self, client, topic
    ):
        user = _verified_user("owner@example.com")
        EmailAddress.objects.create(
            user=user, email="unverified@example.com", verified=False
        )
        UserSubscription.objects.create(
            user=user, topic=topic, status=Status.ACTIVE
        )
        client.force_authenticate(user=user)

        response = _post(client, email="unverified@example.com")

        assert response.json()["status"] == "pending_confirmation"

    @pytest.mark.parametrize(
        "state", [Status.PENDING, Status.UNSUBSCRIBED, Status.BOUNCED, None]
    )
    def test_an_own_address_that_is_not_confirmed_is_not_already_subscribed(
        self, client, topic, state, django_capture_on_commit_callbacks
    ):
        user = _verified_user("owner@example.com")
        if state is not None:
            UserSubscription.objects.create(
                user=user, topic=topic, status=state
            )
        client.force_authenticate(user=user)

        with django_capture_on_commit_callbacks(execute=True):
            response = _post(client, email="owner@example.com")

        assert response.json()["status"] == "pending_confirmation"
        row = UserSubscription.objects.get(user=user, topic=topic)
        assert row.status == Status.PENDING
        assert len(mail.outbox) == 1
