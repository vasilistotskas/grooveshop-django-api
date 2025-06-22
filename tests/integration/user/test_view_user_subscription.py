from django.contrib.auth import get_user_model
from django.urls import reverse
from knox.models import get_token_model
from rest_framework import status
from rest_framework.test import APITestCase

from user.models.subscription import SubscriptionTopic, UserSubscription

User = get_user_model()
AuthToken = get_token_model()


class BaseSubscriptionAPITest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="user@test.com", password="testpass123", username="testuser"
        )
        self.other_user = User.objects.create_user(
            email="other@test.com", password="testpass123", username="otheruser"
        )
        self.admin_user = User.objects.create_superuser(
            email="admin@test.com",
            password="adminpass123",
            username="adminuser",
        )

        self.topics = []
        categories = [
            SubscriptionTopic.TopicCategory.NEWSLETTER,
            SubscriptionTopic.TopicCategory.PRODUCT,
            SubscriptionTopic.TopicCategory.MARKETING,
        ]

        for i in range(5):
            topic = SubscriptionTopic.objects.create(
                slug=f"topic-{i}",
                name=f"Topic {i}",
                description=f"Description for topic {i}",
                category=categories[i % len(categories)],
                is_active=i < 4,
                is_default=i == 0,
                requires_confirmation=i == 1,
            )
            self.topics.append(topic)

        self.user_token = self.get_token_for_user(self.user)
        self.other_user_token = self.get_token_for_user(self.other_user)
        self.admin_token = self.get_token_for_user(self.admin_user)

    def get_token_for_user(self, user):
        _, token = AuthToken.objects.create(user)
        return str(token)

    def authenticate(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def unauthenticate(self):
        self.client.credentials()


class SubscriptionTopicViewSetTest(BaseSubscriptionAPITest):
    def test_list_topics_authenticated(self):
        self.authenticate(self.user_token)

        url = reverse("user-subscription-topic-list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 4)

    def test_list_topics_unauthenticated(self):
        url = reverse("user-subscription-topic-list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_retrieve_topic(self):
        self.authenticate(self.user_token)

        url = reverse(
            "user-subscription-topic-detail", args=[self.topics[0].pk]
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["slug"], "topic-0")
        self.assertIn("subscriber_count", response.data)

    def test_filter_topics_by_category(self):
        self.authenticate(self.user_token)

        url = reverse("user-subscription-topic-list")
        response = self.client.get(url, {"category": "NEWSLETTER"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 2)

    def test_search_topics(self):
        self.authenticate(self.user_token)

        url = reverse("user-subscription-topic-list")
        response = self.client.get(url, {"search": "Topic 1"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

        translations = response.data["results"][0]["translations"]
        first_lang = next(iter(translations.keys()))
        self.assertEqual(translations[first_lang]["name"], "Topic 1")

    def test_my_subscriptions_endpoint(self):
        self.authenticate(self.user_token)

        UserSubscription.objects.create(
            user=self.user,
            topic=self.topics[0],
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )
        UserSubscription.objects.create(
            user=self.user,
            topic=self.topics[1],
            status=UserSubscription.SubscriptionStatus.PENDING,
        )
        UserSubscription.objects.create(
            user=self.user,
            topic=self.topics[2],
            status=UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
        )

        url = reverse("user-subscription-topic-my-subscriptions")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("subscribed", response.data)
        self.assertIn("available", response.data)

        self.assertEqual(len(response.data["subscribed"]), 1)
        self.assertEqual(response.data["subscribed"][0]["slug"], "topic-0")

        available_slugs = [t["slug"] for t in response.data["available"]]
        self.assertIn("topic-3", available_slugs)
        self.assertNotIn("topic-0", available_slugs)

    def test_subscribe_to_topic(self):
        self.authenticate(self.user_token)

        url = reverse(
            "user-subscription-topic-subscribe", args=[self.topics[0].pk]
        )
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "ACTIVE")

        subscription = UserSubscription.objects.get(
            user=self.user, topic=self.topics[0]
        )
        self.assertEqual(
            subscription.status, UserSubscription.SubscriptionStatus.ACTIVE
        )

    def test_subscribe_to_topic_requiring_confirmation(self):
        self.authenticate(self.user_token)

        url = reverse(
            "user-subscription-topic-subscribe",
            kwargs={"pk": self.topics[1].id},
        )
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "PENDING")

        subscription = UserSubscription.objects.get(
            user=self.user, topic=self.topics[1]
        )
        self.assertEqual(
            subscription.status, UserSubscription.SubscriptionStatus.PENDING
        )
        self.assertIsNotNone(subscription.confirmation_token)

    def test_subscribe_already_subscribed(self):
        self.authenticate(self.user_token)

        UserSubscription.objects.create(
            user=self.user,
            topic=self.topics[0],
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        url = reverse(
            "user-subscription-topic-subscribe", args=[self.topics[0].pk]
        )
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Already subscribed", response.data["detail"])

    def test_reactivate_unsubscribed_subscription(self):
        self.authenticate(self.user_token)

        UserSubscription.objects.create(
            user=self.user,
            topic=self.topics[0],
            status=UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
        )

        url = reverse(
            "user-subscription-topic-subscribe", args=[self.topics[0].pk]
        )
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ACTIVE")

    def test_unsubscribe_from_topic(self):
        self.authenticate(self.user_token)

        UserSubscription.objects.create(
            user=self.user,
            topic=self.topics[0],
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        url = reverse(
            "user-subscription-topic-unsubscribe", args=[self.topics[0].pk]
        )
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        subscription = UserSubscription.objects.get(
            user=self.user, topic=self.topics[0]
        )
        self.assertEqual(
            subscription.status,
            UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
        )
        self.assertIsNotNone(subscription.unsubscribed_at)

    def test_unsubscribe_not_subscribed(self):
        self.authenticate(self.user_token)

        url = reverse(
            "user-subscription-topic-unsubscribe", args=[self.topics[0].pk]
        )
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not subscribed", response.data["detail"])

    def test_subscribe_to_inactive_topic(self):
        self.authenticate(self.user_token)

        url = reverse(
            "user-subscription-topic-subscribe",
            kwargs={"pk": self.topics[4].id},
        )
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class UserSubscriptionViewSetTest(BaseSubscriptionAPITest):
    def setUp(self):
        super().setUp()

        self.user_subscriptions = []

        active_sub = UserSubscription.objects.create(
            user=self.user,
            topic=self.topics[0],
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )
        self.user_subscriptions.append(active_sub)

        active_sub2 = UserSubscription.objects.create(
            user=self.user,
            topic=self.topics[1],
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )
        self.user_subscriptions.append(active_sub2)

        pending_sub = UserSubscription.objects.create(
            user=self.user,
            topic=self.topics[2],
            status=UserSubscription.SubscriptionStatus.PENDING,
            confirmation_token="",
        )
        self.user_subscriptions.append(pending_sub)

        other_sub = UserSubscription.objects.create(
            user=self.other_user,
            topic=self.topics[0],
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )
        self.other_user_subscriptions = [other_sub]

    def test_list_user_subscriptions(self):
        self.authenticate(self.user_token)

        url = reverse("user-subscription-list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 3)

        for sub in response.data["results"]:
            self.assertEqual(sub["user"], self.user.id)

    def test_create_subscription(self):
        self.authenticate(self.user_token)

        url = reverse("user-subscription-list")
        data = {"topic": self.topics[3].id, "status": "ACTIVE"}
        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["user"], self.user.id)
        self.assertEqual(response.data["topic"], self.topics[3].id)

    def test_update_subscription_metadata(self):
        self.authenticate(self.user_token)

        url = reverse(
            "user-subscription-detail",
            args=[self.user_subscriptions[0].id],
        )
        data = {"metadata": {"frequency": "weekly", "format": "html"}}
        response = self.client.patch(url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["metadata"]["frequency"], "weekly")

    def test_cannot_update_other_users_subscription(self):
        self.authenticate(self.user_token)

        other_sub = UserSubscription.objects.get(user=self.other_user)

        url = reverse("user-subscription-detail", args=[other_sub.id])
        data = {"status": "UNSUBSCRIBED"}
        response = self.client.patch(url, data)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_filter_subscriptions_by_status(self):
        self.authenticate(self.user_token)

        url = reverse("user-subscription-list")
        response = self.client.get(url, {"status": "ACTIVE"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 2)

        for sub in response.data["results"]:
            self.assertEqual(sub["status"], "ACTIVE")

    def test_bulk_update_subscribe(self):
        self.authenticate(self.user_token)

        url = reverse("user-subscription-bulk-update")
        data = {"topic_ids": [self.topics[3].id], "action": "subscribe"}
        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["success"]), 1)
        self.assertIn("Topic 3", response.data["success"])

        self.assertTrue(
            UserSubscription.objects.filter(
                user=self.user,
                topic=self.topics[3],
                status=UserSubscription.SubscriptionStatus.ACTIVE,
            ).exists()
        )

    def test_bulk_update_unsubscribe(self):
        self.authenticate(self.user_token)

        url = reverse("user-subscription-bulk-update")
        data = {
            "topic_ids": [self.topics[0].id, self.topics[1].id],
            "action": "unsubscribe",
        }
        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["success"]), 2)

        unsubscribed = UserSubscription.objects.filter(
            user=self.user,
            topic__in=[self.topics[0], self.topics[1]],
            status=UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
        )
        self.assertEqual(unsubscribed.count(), 2)

    def test_bulk_update_mixed_results(self):
        self.authenticate(self.user_token)

        url = reverse("user-subscription-bulk-update")
        data = {
            "topic_ids": [
                self.topics[3].id,
                self.topics[0].id,
                self.topics[4].id,
            ],
            "action": "subscribe",
        }
        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(
            "Invalid or inactive topic IDs" in str(response.data),
            f"Expected error about invalid topic IDs, got: {response.data}",
        )

    def test_confirm_subscription(self):
        self.authenticate(self.user_token)

        pending_sub = self.user_subscriptions[2]
        pending_sub.confirmation_token = "test-token-123"
        pending_sub.save()

        url = reverse(
            "user-subscription-confirm",
            args=[pending_sub.id],
        )
        data = {"token": "test-token-123"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ACTIVE")

        pending_sub.refresh_from_db()
        self.assertEqual(
            pending_sub.status, UserSubscription.SubscriptionStatus.ACTIVE
        )
        self.assertEqual(pending_sub.confirmation_token, "")

    def test_confirm_with_invalid_token(self):
        self.authenticate(self.user_token)

        pending_sub = self.user_subscriptions[2]
        pending_sub.confirmation_token = "test-token-123"
        pending_sub.save()

        url = reverse(
            "user-subscription-confirm",
            args=[pending_sub.id],
        )
        data = {"token": "wrong-token"}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid confirmation token", response.data["detail"])

    def test_confirm_non_pending_subscription(self):
        self.authenticate(self.user_token)

        active_sub = self.user_subscriptions[0]

        url = reverse(
            "user-subscription-confirm",
            kwargs={"pk": active_sub.id},
        )
        data = {"token": "any-token"}
        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not pending confirmation", response.data["detail"])

    def test_ordering_subscriptions(self):
        self.authenticate(self.user_token)

        url = reverse("user-subscription-list")

        response = self.client.get(url)
        dates = [sub["subscribed_at"] for sub in response.data["results"]]
        self.assertEqual(dates, sorted(dates, reverse=True))

        response = self.client.get(url, {"ordering": "created_at"})
        dates = [sub["created_at"] for sub in response.data["results"]]
        self.assertEqual(dates, sorted(dates))
