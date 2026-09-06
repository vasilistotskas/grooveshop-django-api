"""A subscription topic is store configuration, not user data.

`SubscriptionTopicViewSet` carried `permission_classes =
[IsAuthenticated]` while `create`, `update`, `partial_update` and
`destroy` were all routed. `IsNewsletterEnabled` could not help — it is
a FEATURE gate that 404s when the merchant switches the feature off and
otherwise returns True, contributing no authorization.

`UserSubscription.topic` is `on_delete=CASCADE`, so a single DELETE from
any registered shopper took the store's entire subscriber list with it.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from user.factories.account import UserAccountFactory
from user.models.subscription import SubscriptionTopic, UserSubscription

pytestmark = pytest.mark.django_db


def _topic_with_subscribers(count=3):
    topic = SubscriptionTopic.objects.create(slug="newsletter", is_active=True)
    for _ in range(count):
        UserSubscription.objects.create(
            user=UserAccountFactory(num_addresses=0), topic=topic
        )
    return topic


def _shopper_client():
    shopper = UserAccountFactory(
        num_addresses=0, is_staff=False, is_superuser=False
    )
    client = APIClient()
    client.force_authenticate(user=shopper)
    return client


def test_a_shopper_cannot_delete_a_topic_or_its_subscribers():
    topic = _topic_with_subscribers()

    response = _shopper_client().delete(
        reverse("user-subscription-topic-detail", args=[topic.id])
    )

    assert response.status_code in (401, 403, 404), response.status_code
    assert UserSubscription.objects.count() == 3, (
        "the cascade ran — a shopper destroyed the subscriber list"
    )
    assert SubscriptionTopic.objects.filter(pk=topic.pk).exists()


def test_a_shopper_cannot_create_a_topic():
    response = _shopper_client().post(
        reverse("user-subscription-topic-list"),
        {"slug": "attacker-topic"},
        format="json",
    )
    assert response.status_code in (401, 403, 404), response.status_code
    assert not SubscriptionTopic.objects.filter(slug="attacker-topic").exists()


def test_a_shopper_cannot_flip_a_topic_to_auto_subscribe_everyone():
    """`is_default` + `requires_confirmation=False` turns the store into
    a non-consented mailer for every future registrant."""
    topic = _topic_with_subscribers(0)

    response = _shopper_client().patch(
        reverse("user-subscription-topic-detail", args=[topic.id]),
        {"isDefault": True, "requiresConfirmation": False},
        format="json",
    )

    assert response.status_code in (401, 403, 404), response.status_code
    topic.refresh_from_db()
    assert not topic.is_default


def test_a_shopper_can_still_read_topics():
    """Reads are unchanged — customers pick what to subscribe to."""
    _topic_with_subscribers(0)
    response = _shopper_client().get(reverse("user-subscription-topic-list"))
    assert response.status_code == 200
