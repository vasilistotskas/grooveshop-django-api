from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from user.models.subscription import SubscriptionTopic, UserSubscription

User = get_user_model()


class SubscriptionTopicModelTestCase(TestCase):
    def setUp(self):
        self.topic = SubscriptionTopic.objects.create(
            slug="test-newsletter",
            name="Test Newsletter",
            description="A test newsletter",
            category=SubscriptionTopic.TopicCategory.NEWSLETTER,
            is_active=True,
            is_default=False,
            requires_confirmation=False,
        )

    def test_str_representation(self):
        expected = f"{self.topic.name} ({self.topic.category})"
        self.assertEqual(str(self.topic), expected)

    def test_unique_slug(self):
        with self.assertRaises(IntegrityError):
            SubscriptionTopic.objects.create(
                slug="test-newsletter",
                name="Another Newsletter",
                category=SubscriptionTopic.TopicCategory.NEWSLETTER,
            )

    def test_default_values(self):
        topic = SubscriptionTopic.objects.create(
            slug="minimal-topic", name="Minimal Topic"
        )
        self.assertTrue(topic.is_active)
        self.assertFalse(topic.is_default)
        self.assertFalse(topic.requires_confirmation)
        self.assertEqual(topic.category, SubscriptionTopic.TopicCategory.OTHER)
        self.assertEqual(topic.description, "")

    def test_category_choices(self):
        categories = [
            SubscriptionTopic.TopicCategory.NEWSLETTER,
            SubscriptionTopic.TopicCategory.PRODUCT,
            SubscriptionTopic.TopicCategory.MARKETING,
            SubscriptionTopic.TopicCategory.SYSTEM,
            SubscriptionTopic.TopicCategory.OTHER,
        ]

        for i, category in enumerate(categories):
            topic = SubscriptionTopic.objects.create(
                slug=f"topic-{i}", name=f"Topic {i}", category=category
            )
            self.assertEqual(topic.category, category)

    def test_subscriber_count_with_no_subscribers(self):
        self.assertEqual(self.topic.subscribers.count(), 0)

    def test_subscriber_count_with_mixed_statuses(self):
        user1 = User.objects.create_user(
            email="user1@test.com", password="pass"
        )
        user2 = User.objects.create_user(
            email="user2@test.com", password="pass"
        )
        user3 = User.objects.create_user(
            email="user3@test.com", password="pass"
        )

        UserSubscription.objects.create(
            user=user1,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        UserSubscription.objects.create(
            user=user2,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
        )

        UserSubscription.objects.create(
            user=user3,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
        )

        active_count = self.topic.subscribers.filter(
            status=UserSubscription.SubscriptionStatus.ACTIVE
        ).count()
        self.assertEqual(active_count, 1)
        self.assertEqual(self.topic.subscribers.count(), 3)


class UserSubscriptionModelTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="test@example.com", password="testpass123"
        )
        self.topic = SubscriptionTopic.objects.create(
            slug="test-topic",
            name="Test Topic",
            category=SubscriptionTopic.TopicCategory.NEWSLETTER,
        )

    def test_str_representation(self):
        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )
        expected = f"{self.user} - {self.topic} ({subscription.status})"
        self.assertEqual(str(subscription), expected)

    def test_unique_together_constraint(self):
        UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        with self.assertRaises(IntegrityError):
            UserSubscription.objects.create(
                user=self.user,
                topic=self.topic,
                status=UserSubscription.SubscriptionStatus.ACTIVE,
            )

    def test_default_values(self):
        subscription = UserSubscription.objects.create(
            user=self.user, topic=self.topic
        )
        self.assertEqual(
            subscription.status, UserSubscription.SubscriptionStatus.ACTIVE
        )
        self.assertIsNone(subscription.unsubscribed_at)
        self.assertEqual(subscription.confirmation_token, "")
        self.assertEqual(subscription.metadata, {})

    def test_unsubscribe_method(self):
        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        time_before = timezone.now()

        subscription.unsubscribe()

        subscription.refresh_from_db()

        self.assertEqual(
            subscription.status,
            UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
        )
        self.assertIsNotNone(subscription.unsubscribed_at)
        self.assertGreaterEqual(subscription.unsubscribed_at, time_before)

    def test_activate_method(self):
        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
            confirmation_token="test-token-123",
        )

        subscription.activate()

        subscription.refresh_from_db()

        self.assertEqual(
            subscription.status, UserSubscription.SubscriptionStatus.ACTIVE
        )
        self.assertEqual(subscription.confirmation_token, "")

    def test_status_choices(self):
        statuses = [
            UserSubscription.SubscriptionStatus.ACTIVE,
            UserSubscription.SubscriptionStatus.PENDING,
            UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
            UserSubscription.SubscriptionStatus.BOUNCED,
        ]

        for i, status in enumerate(statuses):
            user = User.objects.create_user(
                email=f"user{i}@test.com", password="pass"
            )
            subscription = UserSubscription.objects.create(
                user=user, topic=self.topic, status=status
            )
            self.assertEqual(subscription.status, status)

    def test_metadata_field(self):
        metadata = {
            "frequency": "weekly",
            "format": "html",
            "custom_preferences": ["updates", "tips"],
        }

        subscription = UserSubscription.objects.create(
            user=self.user, topic=self.topic, metadata=metadata
        )

        subscription.refresh_from_db()

        self.assertEqual(subscription.metadata, metadata)
        self.assertEqual(subscription.metadata["frequency"], "weekly")
        self.assertIn("updates", subscription.metadata["custom_preferences"])

    def test_cascade_delete_user(self):
        subscription = UserSubscription.objects.create(
            user=self.user, topic=self.topic
        )

        subscription_id = subscription.id
        self.user.delete()

        self.assertFalse(
            UserSubscription.objects.filter(id=subscription_id).exists()
        )

    def test_cascade_delete_topic(self):
        subscription = UserSubscription.objects.create(
            user=self.user, topic=self.topic
        )

        subscription_id = subscription.id
        self.topic.delete()

        self.assertFalse(
            UserSubscription.objects.filter(id=subscription_id).exists()
        )


class UserAccountSubscriptionIntegrationTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="test@example.com",
            password="testpass123",
            username="testuser",
        )

        self.topics = []
        for i in range(3):
            topic = SubscriptionTopic.objects.create(
                slug=f"topic-{i}",
                name=f"Topic {i}",
                category=SubscriptionTopic.TopicCategory.NEWSLETTER,
            )
            self.topics.append(topic)

    def test_active_subscriptions_property(self):
        UserSubscription.objects.create(
            user=self.user,
            topic=self.topics[0],
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )
        UserSubscription.objects.create(
            user=self.user,
            topic=self.topics[1],
            status=UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
        )
        UserSubscription.objects.create(
            user=self.user,
            topic=self.topics[2],
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        active_subs = self.user.active_subscriptions
        self.assertEqual(active_subs.count(), 2)

        for sub in active_subs:
            self.assertEqual(
                sub.status, UserSubscription.SubscriptionStatus.ACTIVE
            )

    def test_subscription_preferences_property(self):
        UserSubscription.objects.create(
            user=self.user,
            topic=self.topics[0],
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )
        UserSubscription.objects.create(
            user=self.user,
            topic=self.topics[1],
            status=UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
        )

        prefs = self.user.subscription_preferences

        self.assertIsInstance(prefs, dict)
        self.assertTrue(prefs.get("topic-0"))
        self.assertFalse(prefs.get("topic-1"))
        self.assertIsNone(prefs.get("topic-2"))
