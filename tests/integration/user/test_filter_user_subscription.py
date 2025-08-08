from datetime import timedelta
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from user.factories.subscription import (
    SubscriptionTopicFactory,
    UserSubscriptionFactory,
)
from user.factories.account import UserAccountFactory
from user.models.subscription import SubscriptionTopic, UserSubscription

User = get_user_model()


class UserSubscriptionFilterTest(APITestCase):
    def setUp(self):
        UserSubscription.objects.all().delete()
        SubscriptionTopic.objects.all().delete()
        User.objects.all().delete()

        self.user = UserAccountFactory()
        self.other_user = UserAccountFactory()

        self.now = timezone.now()

        self.newsletter_topic = SubscriptionTopicFactory(
            slug="weekly-newsletter",
            category=SubscriptionTopic.TopicCategory.NEWSLETTER,
            is_active=True,
        )
        self.newsletter_topic.set_current_language("en")
        self.newsletter_topic.name = "Weekly Newsletter"
        self.newsletter_topic.description = "Get our weekly updates"
        self.newsletter_topic.save()

        self.marketing_topic = SubscriptionTopicFactory(
            slug="marketing-campaigns",
            category=SubscriptionTopic.TopicCategory.MARKETING,
            is_active=True,
        )
        self.marketing_topic.set_current_language("en")
        self.marketing_topic.name = "Marketing Updates"
        self.marketing_topic.description = "Special offers and promotions"
        self.marketing_topic.save()

        self.product_topic = SubscriptionTopicFactory(
            slug="product-updates",
            category=SubscriptionTopic.TopicCategory.PRODUCT,
            is_active=True,
        )
        self.product_topic.set_current_language("en")
        self.product_topic.name = "Product News"
        self.product_topic.description = "Latest product features"
        self.product_topic.save()

        self.active_subscription = UserSubscriptionFactory(
            user=self.user,
            topic=self.newsletter_topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
            confirmation_token="",
            metadata={
                "source": "website",
                "preferences": {"frequency": "weekly"},
            },
        )
        self.active_subscription.created_at = self.now - timedelta(days=30)
        self.active_subscription.subscribed_at = self.now - timedelta(days=30)
        self.active_subscription.save()

        self.pending_subscription = UserSubscriptionFactory(
            user=self.user,
            topic=self.marketing_topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
            confirmation_token="abc123token",
            metadata={},
        )
        self.pending_subscription.created_at = self.now - timedelta(days=15)
        self.pending_subscription.subscribed_at = self.now - timedelta(days=15)
        self.pending_subscription.save()

        self.unsubscribed_subscription = UserSubscriptionFactory(
            user=self.user,
            topic=self.product_topic,
            status=UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
            confirmation_token="",
            metadata={"unsubscribe_reason": "too_frequent"},
        )
        self.unsubscribed_subscription.created_at = self.now - timedelta(days=5)
        self.unsubscribed_subscription.subscribed_at = self.now - timedelta(
            days=5
        )
        self.unsubscribed_subscription.unsubscribed_at = self.now - timedelta(
            days=2
        )
        self.unsubscribed_subscription.save()

        self.other_user_subscription = UserSubscriptionFactory(
            user=self.other_user,
            topic=self.newsletter_topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
            confirmation_token="",
            metadata={"source": "mobile_app"},
        )
        self.other_user_subscription.created_at = self.now - timedelta(days=10)
        self.other_user_subscription.subscribed_at = self.now - timedelta(
            days=10
        )
        self.other_user_subscription.save()

        self.client.force_authenticate(user=self.user)

    def test_timestamp_filters(self):
        url = reverse("user-subscription-list")

        created_after_date = self.now - timedelta(days=20)
        response = self.client.get(
            url,
            {"created_after": created_after_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertNotIn(self.active_subscription.id, result_ids)
        self.assertIn(self.pending_subscription.id, result_ids)
        self.assertIn(self.unsubscribed_subscription.id, result_ids)
        self.assertEqual(len(result_ids), 2)

        created_before_date = self.now - timedelta(days=20)
        response = self.client.get(
            url,
            {"created_before": created_before_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.active_subscription.id, result_ids)

    def test_uuid_filter(self):
        url = reverse("user-subscription-list")

        response = self.client.get(
            url, {"uuid": str(self.active_subscription.uuid)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.active_subscription.id
        )

    def test_camel_case_filters(self):
        url = reverse("user-subscription-list")

        created_after_date = self.now - timedelta(days=20)
        response = self.client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "status": UserSubscription.SubscriptionStatus.PENDING,
            },
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.pending_subscription.id, result_ids)

        response = self.client.get(
            url,
            {
                "subscribedAfter": (self.now - timedelta(days=20)).isoformat(),
                "topicCategory": SubscriptionTopic.TopicCategory.NEWSLETTER,
            },
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 0)

    def test_status_filters(self):
        url = reverse("user-subscription-list")

        response = self.client.get(
            url, {"status": UserSubscription.SubscriptionStatus.ACTIVE}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.active_subscription.id, result_ids)

        response = self.client.get(
            url, {"status": UserSubscription.SubscriptionStatus.PENDING}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.pending_subscription.id, result_ids)

        response = self.client.get(
            url, {"status": UserSubscription.SubscriptionStatus.UNSUBSCRIBED}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.unsubscribed_subscription.id, result_ids)

    def test_topic_filters(self):
        url = reverse("user-subscription-list")

        response = self.client.get(url, {"topic": self.newsletter_topic.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.active_subscription.id, result_ids)

        response = self.client.get(
            url, {"topic_category": SubscriptionTopic.TopicCategory.MARKETING}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.pending_subscription.id, result_ids)

    def test_subscription_date_filters(self):
        url = reverse("user-subscription-list")

        subscribed_after_date = self.now - timedelta(days=20)
        response = self.client.get(
            url,
            {"subscribed_after": subscribed_after_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.pending_subscription.id, result_ids)
        self.assertIn(self.unsubscribed_subscription.id, result_ids)

        unsubscribed_after_date = self.now - timedelta(days=3)
        response = self.client.get(
            url,
            {"unsubscribed_after": unsubscribed_after_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.unsubscribed_subscription.id, result_ids)

    def test_topic_slug_filters(self):
        url = reverse("user-subscription-list")

        response = self.client.get(url, {"topic_slug": "newsletter"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.active_subscription.id, result_ids)

        response = self.client.get(
            url, {"topic_slug_exact": "marketing-campaigns"}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.pending_subscription.id, result_ids)

    def test_topic_translation_filters(self):
        url = reverse("user-subscription-list")

        response = self.client.get(url, {"topic_name": "Newsletter"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.active_subscription.id, result_ids)

        response = self.client.get(url, {"topic_description": "offers"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.pending_subscription.id, result_ids)

    def test_custom_filters(self):
        url = reverse("user-subscription-list")

        response = self.client.get(url, {"is_confirmed": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.active_subscription.id, result_ids)
        self.assertIn(self.unsubscribed_subscription.id, result_ids)

        response = self.client.get(url, {"is_confirmed": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.pending_subscription.id, result_ids)

        response = self.client.get(url, {"has_metadata": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.active_subscription.id, result_ids)
        self.assertIn(self.unsubscribed_subscription.id, result_ids)

        response = self.client.get(url, {"has_metadata": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.pending_subscription.id, result_ids)

    def test_complex_filter_combinations(self):
        url = reverse("user-subscription-list")

        created_after_date = self.now - timedelta(days=25)
        response = self.client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "status": UserSubscription.SubscriptionStatus.PENDING,
                "topicCategory": SubscriptionTopic.TopicCategory.MARKETING,
                "isConfirmed": "false",
                "ordering": "-createdAt",
            },
        )

        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.pending_subscription.id, result_ids)

        response = self.client.get(
            url,
            {
                "topicName": "Newsletter",
                "hasMetadata": "true",
                "status": UserSubscription.SubscriptionStatus.ACTIVE,
                "isConfirmed": "true",
            },
        )

        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.active_subscription.id, result_ids)

    def test_filter_with_ordering(self):
        url = reverse("user-subscription-list")

        response = self.client.get(url, {"ordering": "-createdAt"})
        self.assertEqual(response.status_code, 200)

        results = response.data["results"]
        self.assertEqual(len(results), 3)

        subscription_ids = [r["id"] for r in results]
        self.assertEqual(subscription_ids[0], self.unsubscribed_subscription.id)
        self.assertEqual(subscription_ids[1], self.pending_subscription.id)
        self.assertEqual(subscription_ids[2], self.active_subscription.id)

        response = self.client.get(url, {"ordering": "status,-subscribedAt"})
        self.assertEqual(response.status_code, 200)

        results = response.data["results"]
        self.assertEqual(len(results), 3)
        statuses = [r["status"] for r in results]
        self.assertTrue(statuses.index("ACTIVE") < statuses.index("PENDING"))

    def test_existing_filters_still_work(self):
        url = reverse("user-subscription-list")

        response = self.client.get(
            url,
            {
                "status": UserSubscription.SubscriptionStatus.ACTIVE,
                "topic_category": SubscriptionTopic.TopicCategory.NEWSLETTER,
                "is_confirmed": "true",
            },
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.active_subscription.id, result_ids)

    def test_user_isolation(self):
        url = reverse("user-subscription-list")

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.active_subscription.id, result_ids)
        self.assertIn(self.pending_subscription.id, result_ids)
        self.assertIn(self.unsubscribed_subscription.id, result_ids)
        self.assertNotIn(self.other_user_subscription.id, result_ids)

        self.client.force_authenticate(user=self.other_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertGreaterEqual(len(result_ids), 1)
        self.assertIn(self.other_user_subscription.id, result_ids)
        self.assertNotIn(self.active_subscription.id, result_ids)
        self.assertNotIn(self.pending_subscription.id, result_ids)
        self.assertNotIn(self.unsubscribed_subscription.id, result_ids)

    def tearDown(self):
        UserSubscription.objects.all().delete()
        SubscriptionTopic.objects.all().delete()
