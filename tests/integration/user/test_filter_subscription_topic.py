from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from user.factories.account import UserAccountFactory
from user.factories.subscription import (
    SubscriptionTopicFactory,
    UserSubscriptionFactory,
)
from user.models.subscription import SubscriptionTopic, UserSubscription


class SubscriptionTopicFilterTest(APITestCase):
    @classmethod
    def setUpTestData(cls):

        cls.now = timezone.now()

        cls.newsletter_topic = SubscriptionTopicFactory(
            slug="weekly-newsletter",
            category=SubscriptionTopic.TopicCategory.NEWSLETTER,
            is_active=True,
            is_default=True,
            requires_confirmation=False,
            set_translations=False,
        )
        cls.newsletter_topic.created_at = cls.now - timedelta(days=30)
        cls.newsletter_topic.save()

        cls.newsletter_topic.set_current_language("en")
        cls.newsletter_topic.name = "Weekly Newsletter"
        cls.newsletter_topic.description = "Get our weekly updates and news"
        cls.newsletter_topic.save()

        cls.marketing_topic = SubscriptionTopicFactory(
            slug="marketing-campaigns",
            category=SubscriptionTopic.TopicCategory.MARKETING,
            is_active=True,
            is_default=False,
            requires_confirmation=True,
            set_translations=False,
        )
        cls.marketing_topic.created_at = cls.now - timedelta(days=15)
        cls.marketing_topic.save()

        cls.marketing_topic.set_current_language("en")
        cls.marketing_topic.name = "Marketing Updates"
        cls.marketing_topic.description = (
            "Special offers and promotional content"
        )
        cls.marketing_topic.save()

        cls.product_topic = SubscriptionTopicFactory(
            slug="product-updates",
            category=SubscriptionTopic.TopicCategory.PRODUCT,
            is_active=False,
            is_default=False,
            requires_confirmation=False,
            set_translations=False,
        )
        cls.product_topic.created_at = cls.now - timedelta(days=5)
        cls.product_topic.save()

        cls.product_topic.set_current_language("en")
        cls.product_topic.name = "Product News"
        cls.product_topic.description = "Latest product features and updates"
        cls.product_topic.save()

        cls.system_topic = SubscriptionTopicFactory(
            slug="system-notifications",
            category=SubscriptionTopic.TopicCategory.SYSTEM,
            is_active=True,
            is_default=True,
            requires_confirmation=False,
            set_translations=False,
        )
        cls.system_topic.created_at = cls.now - timedelta(hours=2)
        cls.system_topic.save()

        cls.system_topic.set_current_language("en")
        cls.system_topic.name = "System Alerts"
        cls.system_topic.description = (
            "Important system notifications and maintenance updates"
        )
        cls.system_topic.save()

        cls.user1 = UserAccountFactory()
        cls.user2 = UserAccountFactory()

    def setUp(self):
        self.client.force_authenticate(user=self.user1)

    def test_timestamp_filters(self):
        url = reverse("user-subscription-topic-list")

        created_after_date = self.now - timedelta(days=20)
        response = self.client.get(
            url,
            {"created_after": created_after_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertNotIn(self.newsletter_topic.id, result_ids)
        self.assertIn(self.marketing_topic.id, result_ids)
        self.assertIn(self.system_topic.id, result_ids)
        self.assertGreaterEqual(len(result_ids), 2)

        created_before_date = self.now - timedelta(days=20)
        response = self.client.get(
            url,
            {"created_before": created_before_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.newsletter_topic.id, result_ids)
        self.assertNotIn(self.marketing_topic.id, result_ids)
        self.assertNotIn(self.system_topic.id, result_ids)

    def test_uuid_filter(self):
        url = reverse("user-subscription-topic-list")

        response = self.client.get(
            url, {"uuid": str(self.newsletter_topic.uuid)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.newsletter_topic.id
        )

    def test_camel_case_filters(self):
        url = reverse("user-subscription-topic-list")

        created_after_date = self.now - timedelta(days=20)
        response = self.client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "isActive": "true",
            },
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.marketing_topic.id, result_ids)
        self.assertIn(self.system_topic.id, result_ids)

        response = self.client.get(
            url,
            {
                "isDefault": "true",
                "requiresConfirmation": "false",
            },
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.newsletter_topic.id, result_ids)
        self.assertIn(self.system_topic.id, result_ids)

    def test_category_filters(self):
        url = reverse("user-subscription-topic-list")

        response = self.client.get(
            url, {"category": SubscriptionTopic.TopicCategory.NEWSLETTER}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.newsletter_topic.id, result_ids)

        response = self.client.get(
            url, {"category": SubscriptionTopic.TopicCategory.MARKETING}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.marketing_topic.id, result_ids)

    def test_boolean_filters(self):
        url = reverse("user-subscription-topic-list")

        response = self.client.get(url, {"is_active": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertGreaterEqual(len(result_ids), 3)
        self.assertIn(self.newsletter_topic.id, result_ids)
        self.assertIn(self.marketing_topic.id, result_ids)
        self.assertIn(self.system_topic.id, result_ids)
        self.assertNotIn(self.product_topic.id, result_ids)

        response = self.client.get(url, {"is_active": "false"})
        self.assertEqual(response.status_code, 200)

        self.assertFalse(
            self.product_topic.is_active, "Product topic should be inactive"
        )

        response = self.client.get(url, {"is_default": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertGreaterEqual(len(result_ids), 2)
        self.assertIn(self.newsletter_topic.id, result_ids)
        self.assertIn(self.system_topic.id, result_ids)

        response = self.client.get(url, {"requires_confirmation": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertGreaterEqual(len(result_ids), 1)
        self.assertIn(self.marketing_topic.id, result_ids)

    def test_slug_filters(self):
        url = reverse("user-subscription-topic-list")

        response = self.client.get(url, {"slug": "newsletter"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertGreaterEqual(len(result_ids), 1)
        self.assertIn(self.newsletter_topic.id, result_ids)

        response = self.client.get(url, {"slug_exact": "weekly-newsletter"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.newsletter_topic.id, result_ids)

        response = self.client.get(url, {"slug": "system"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertGreaterEqual(len(result_ids), 1)
        self.assertIn(self.system_topic.id, result_ids)

    def test_translation_filters(self):
        url = reverse("user-subscription-topic-list")

        response = self.client.get(url, {"name": "Newsletter"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.newsletter_topic.id, result_ids)

        response = self.client.get(url, {"name": "Updates"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.marketing_topic.id, result_ids)

        response = self.client.get(url, {"description": "weekly"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.newsletter_topic.id, result_ids)

        response = self.client.get(url, {"description": "system"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.system_topic.id, result_ids)

    def test_has_subscribers_filter(self):
        url = reverse("user-subscription-topic-list")
        UserSubscription.objects.all().delete()
        UserSubscriptionFactory(user=self.user1, topic=self.newsletter_topic)
        UserSubscriptionFactory(user=self.user1, topic=self.marketing_topic)

        response = self.client.get(url, {"has_subscribers": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertGreaterEqual(len(result_ids), 2)
        self.assertIn(self.newsletter_topic.id, result_ids)
        self.assertIn(self.marketing_topic.id, result_ids)

        response = self.client.get(url, {"has_subscribers": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertGreaterEqual(len(result_ids), 1)
        self.assertIn(self.system_topic.id, result_ids)

    def test_complex_filter_combinations(self):
        url = reverse("user-subscription-topic-list")

        created_after_date = self.now - timedelta(days=25)
        response = self.client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "isActive": "true",
                "category": SubscriptionTopic.TopicCategory.MARKETING,
                "requiresConfirmation": "true",
                "ordering": "-createdAt",
            },
        )

        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.marketing_topic.id, result_ids)

        response = self.client.get(
            url,
            {
                "name": "Newsletter",
                "category": SubscriptionTopic.TopicCategory.NEWSLETTER,
                "isDefault": "true",
            },
        )

        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.newsletter_topic.id, result_ids)

    def test_filter_with_ordering(self):
        url = reverse("user-subscription-topic-list")

        response = self.client.get(url, {"ordering": "-createdAt"})
        self.assertEqual(response.status_code, 200)

        results = response.data["results"]
        self.assertGreaterEqual(len(results), 3)

        topic_ids = [r["id"] for r in results]
        system_index = topic_ids.index(self.system_topic.id)
        newsletter_index = topic_ids.index(self.newsletter_topic.id)
        self.assertLess(
            system_index,
            newsletter_index,
            "System topic should come before newsletter topic when ordered by -createdAt",
        )

        response = self.client.get(url, {"ordering": "category,slug"})
        self.assertEqual(response.status_code, 200)

        results = response.data["results"]
        self.assertGreaterEqual(len(results), 3)
        categories = [r["category"] for r in results]
        if "MARKETING" in categories and "NEWSLETTER" in categories:
            self.assertLess(
                categories.index("MARKETING"),
                categories.index("NEWSLETTER"),
                "Marketing should come before Newsletter alphabetically",
            )

    def test_existing_filters_still_work(self):
        url = reverse("user-subscription-topic-list")

        response = self.client.get(
            url,
            {
                "is_active": "true",
                "is_default": "true",
                "category": SubscriptionTopic.TopicCategory.NEWSLETTER,
            },
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.newsletter_topic.id, result_ids)
