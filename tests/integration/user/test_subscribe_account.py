"""One rule for subscribing an account: ``subscribe_account``.

A topic that requires confirmation gets a PENDING row with a live link
and its email — never a bare PENDING row, never ACTIVE without the
click — whichever door the request came through: the topic's subscribe
action, the bulk update, or the default topics at signup.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from user.factories.account import UserAccountFactory
from user.models.subscription import SubscriptionTopic, UserSubscription
from user.services.subscription import SubscribeOutcome, subscribe_account

pytestmark = pytest.mark.django_db

Status = UserSubscription.SubscriptionStatus


@pytest.fixture
def confirm_topic():
    return SubscriptionTopic.objects.create(
        slug="confirm-me",
        name="Confirm me",
        category=SubscriptionTopic.TopicCategory.NEWSLETTER,
        requires_confirmation=True,
    )


@pytest.fixture
def plain_topic():
    return SubscriptionTopic.objects.create(
        slug="plain",
        name="Plain",
        category=SubscriptionTopic.TopicCategory.ACCOUNT,
    )


def _assert_armed(subscription):
    subscription.refresh_from_db()
    assert subscription.status == Status.PENDING
    assert len(subscription.confirmation_token) == 64
    assert subscription.confirmation_sent_at is not None


class TestHelper:
    def test_confirmation_topic_is_armed_and_mailed(self, confirm_topic):
        user = UserAccountFactory()

        result = subscribe_account(user, confirm_topic)

        assert result.outcome == SubscribeOutcome.PENDING_CONFIRMATION
        assert result.created is True
        _assert_armed(result.subscription)
        assert [m.to for m in mail.outbox] == [[user.email]]

    def test_plain_topic_is_active(self, plain_topic):
        result = subscribe_account(UserAccountFactory(), plain_topic)

        assert result.outcome == SubscribeOutcome.SUBSCRIBED
        assert result.subscription.status == Status.ACTIVE

    def test_unsubscribed_row_on_a_confirmation_topic_is_rearmed(
        self, confirm_topic
    ):
        user = UserAccountFactory()
        row = UserSubscription.objects.create(
            user=user, topic=confirm_topic, status=Status.UNSUBSCRIBED
        )

        result = subscribe_account(user, confirm_topic)

        assert result.created is False
        assert result.subscription.pk == row.pk
        _assert_armed(row)

    @pytest.mark.parametrize(
        ("state", "outcome"),
        [
            (Status.ACTIVE, SubscribeOutcome.ALREADY_ACTIVE),
            (Status.PENDING, SubscribeOutcome.ALREADY_PENDING),
        ],
    )
    def test_active_and_pending_rows_are_left_alone(
        self, confirm_topic, state, outcome
    ):
        user = UserAccountFactory()
        row = UserSubscription.objects.create(
            user=user,
            topic=confirm_topic,
            status=state,
            confirmation_token="kept",
        )

        result = subscribe_account(user, confirm_topic)

        assert result.outcome == outcome
        row.refresh_from_db()
        assert row.status == state
        assert row.confirmation_token == "kept"
        assert mail.outbox == []


class TestCallers:
    def test_bulk_update_arms_a_confirmation_topic(self, confirm_topic):
        user = UserAccountFactory()
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            reverse("user-subscription-bulk-update"),
            {"topic_ids": [confirm_topic.id], "action": "subscribe"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] == [confirm_topic.name]
        _assert_armed(UserSubscription.objects.get(user=user))

    def test_bulk_update_does_not_confirm_a_pending_row(self, confirm_topic):
        user = UserAccountFactory()
        row = UserSubscription.objects.create(
            user=user, topic=confirm_topic, status=Status.PENDING
        )
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            reverse("user-subscription-bulk-update"),
            {"topic_ids": [confirm_topic.id], "action": "subscribe"},
            format="json",
        )

        assert response.data["already_processed"] == [confirm_topic.name]
        row.refresh_from_db()
        assert row.status == Status.PENDING

    def test_topic_resubscribe_rearms_instead_of_activating(
        self, confirm_topic
    ):
        user = UserAccountFactory()
        row = UserSubscription.objects.create(
            user=user, topic=confirm_topic, status=Status.UNSUBSCRIBED
        )
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            reverse(
                "user-subscription-topic-subscribe", args=[confirm_topic.pk]
            )
        )

        assert response.status_code == status.HTTP_200_OK
        _assert_armed(row)
