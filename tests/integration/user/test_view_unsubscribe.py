"""Integration tests for the token-based unsubscribe link endpoints.

These endpoints back the RFC 8058 ``List-Unsubscribe`` links embedded in
marketing/notification emails. The token is a ``django.core.signing`` value
carrying the user's pk (NOT the password-reset generator), so the link keeps
working across logins/password changes and lasts far longer than the old
3-day password-reset window.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.core import signing
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from user.factories.account import UserAccountFactory
from user.models.subscription import SubscriptionTopic, UserSubscription
from user.utils.subscription import (
    UNSUBSCRIBE_SALT,
    generate_blanket_unsubscribe_link,
    generate_unsubscribe_link,
)


@override_settings(API_BASE_URL="https://api.test-site.com")
class UnsubscribeLinkViewTestCase(APITestCase):
    def setUp(self):
        self.user = UserAccountFactory()
        self.topic = SubscriptionTopic.objects.create(
            name="Newsletter",
            slug="newsletter",
            description="News",
            category="news",
        )
        self.subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

    def _token(self) -> str:
        return signing.dumps(self.user.pk, salt=UNSUBSCRIBE_SALT)

    def _topic_url(self, token: str) -> str:
        return reverse(
            "user-unsubscribe-topic",
            kwargs={"token": token, "topic_slug": self.topic.slug},
        )

    def _blanket_url(self, token: str) -> str:
        return reverse("user-unsubscribe", kwargs={"token": token})

    def test_get_topic_unsubscribe_valid_token(self):
        response = self.client.get(self._topic_url(self._token()))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.subscription.refresh_from_db()
        self.assertEqual(
            self.subscription.status,
            UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
        )

    def test_generated_topic_link_is_resolvable_and_works(self):
        """The URL produced by ``generate_unsubscribe_link`` resolves to the
        endpoint and unsubscribes — end-to-end contract check."""
        url = generate_unsubscribe_link(self.user, self.topic)
        path = url.split("https://api.test-site.com")[1]

        response = self.client.get(path)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(
            self.subscription.status,
            UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
        )

    def test_get_blanket_unsubscribe_drops_all(self):
        other_topic = SubscriptionTopic.objects.create(
            name="Deals", slug="deals", description="d", category="promo"
        )
        other_sub = UserSubscription.objects.create(
            user=self.user,
            topic=other_topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        url = generate_blanket_unsubscribe_link(self.user)
        path = url.split("https://api.test-site.com")[1]
        response = self.client.get(path)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        other_sub.refresh_from_db()
        self.assertEqual(
            self.subscription.status,
            UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
        )
        self.assertEqual(
            other_sub.status,
            UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
        )

    def test_get_tampered_token_rejected(self):
        response = self.client.get(self._topic_url("not-a-valid-token"))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.subscription.refresh_from_db()
        self.assertEqual(
            self.subscription.status,
            UserSubscription.SubscriptionStatus.ACTIVE,
        )

    def test_get_token_for_deleted_user_rejected(self):
        token = signing.dumps(999999, salt=UNSUBSCRIBE_SALT)
        response = self.client.get(self._topic_url(token))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_expired_token_rejected(self):
        # Force the age check to fail deterministically without sleeping.
        with mock.patch(
            "user.views.subscription.UNSUBSCRIBE_MAX_AGE",
            timedelta(seconds=-1),
        ):
            response = self.client.get(self._topic_url(self._token()))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.subscription.refresh_from_db()
        self.assertEqual(
            self.subscription.status,
            UserSubscription.SubscriptionStatus.ACTIVE,
        )

    def test_post_one_click_valid_token_unsubscribes(self):
        response = self.client.post(self._topic_url(self._token()))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(
            self.subscription.status,
            UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
        )

    def test_post_one_click_invalid_token_is_silent_200(self):
        """RFC 8058: POST always returns 200 to avoid leaking token
        validity to inbox scanners — even for a bad token."""
        response = self.client.post(self._topic_url("bogus"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(
            self.subscription.status,
            UserSubscription.SubscriptionStatus.ACTIVE,
        )
