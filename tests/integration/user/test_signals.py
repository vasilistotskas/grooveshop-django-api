from django.contrib.auth import get_user_model
from django.test import TestCase

from user.models.subscription import SubscriptionTopic, UserSubscription

User = get_user_model()


class CreateDefaultSubscriptionsSignalTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.default_topics = []
        cls.non_default_topics = []

        # The allowlisted categories: only these are ever subscribed
        # automatically (see test_marketing_defaults_need_consent).
        for i, category in enumerate(
            (
                SubscriptionTopic.TopicCategory.ACCOUNT,
                SubscriptionTopic.TopicCategory.SYSTEM,
                SubscriptionTopic.TopicCategory.SYSTEM,
            )
        ):
            topic = SubscriptionTopic.objects.create(
                slug=f"default-topic-{i}",
                name=f"Default Topic {i}",
                category=category,
                is_active=True,
                is_default=True,
            )
            cls.default_topics.append(topic)

        for i in range(2):
            topic = SubscriptionTopic.objects.create(
                slug=f"non-default-topic-{i}",
                name=f"Non-Default Topic {i}",
                category=SubscriptionTopic.TopicCategory.PRODUCT,
                is_active=True,
                is_default=False,
            )
            cls.non_default_topics.append(topic)

        cls.inactive_default = SubscriptionTopic.objects.create(
            slug="inactive-default",
            name="Inactive Default Topic",
            category=SubscriptionTopic.TopicCategory.SYSTEM,
            is_active=False,
            is_default=True,
        )

    def test_new_user_gets_default_subscriptions(self):
        user = User.objects.create_user(
            email="newuser@test.com", password="testpass123", username="newuser"
        )

        subscriptions = UserSubscription.objects.filter(user=user)
        self.assertEqual(subscriptions.count(), 3)

        subscribed_topics = {sub.topic for sub in subscriptions}
        expected_topics = set(self.default_topics)
        self.assertEqual(subscribed_topics, expected_topics)

        for subscription in subscriptions:
            self.assertEqual(
                subscription.status, UserSubscription.SubscriptionStatus.ACTIVE
            )
            self.assertEqual(
                subscription.source, UserSubscription.Source.SIGNUP
            )

    def test_marketing_defaults_need_consent(self):
        """Creating an account is not consent to marketing mail: only the
        allowlisted service categories (account, system) auto-subscribe.
        Product updates, "other", marketing, newsletter and promotional
        topics subscribe nobody, whatever their ``is_default`` says."""
        marketing = [
            SubscriptionTopic.objects.create(
                slug=f"marketing-{category.lower()}",
                name=f"Marketing {category}",
                category=category,
                is_active=True,
                is_default=True,
            )
            for category in SubscriptionTopic.TopicCategory
            if category not in SubscriptionTopic.AUTO_SUBSCRIBE_CATEGORIES
        ]
        # Product updates and "other" are refused explicitly: they are the
        # two a denylist got wrong.
        assert {topic.category for topic in marketing} >= {
            SubscriptionTopic.TopicCategory.PRODUCT,
            SubscriptionTopic.TopicCategory.OTHER,
        }

        user = User.objects.create_user(
            email="consent@test.com", password="testpass123", username="c"
        )

        self.assertFalse(
            UserSubscription.objects.filter(
                user=user, topic__in=marketing
            ).exists()
        )
        self.assertEqual(UserSubscription.objects.filter(user=user).count(), 3)

    def test_default_topic_requiring_confirmation_is_armed(self):
        topic = SubscriptionTopic.objects.create(
            slug="confirm-me",
            name="Confirm Me",
            category=SubscriptionTopic.TopicCategory.ACCOUNT,
            is_active=True,
            is_default=True,
            requires_confirmation=True,
        )

        user = User.objects.create_user(
            email="armed@test.com", password="testpass123", username="armed"
        )

        subscription = UserSubscription.objects.get(user=user, topic=topic)
        self.assertEqual(
            subscription.status, UserSubscription.SubscriptionStatus.PENDING
        )
        self.assertEqual(len(subscription.confirmation_token), 64)
        self.assertIsNotNone(subscription.confirmation_sent_at)

    def test_signal_only_on_create(self):
        user = User.objects.create_user(
            email="testcreate@test.com",
            password="testpass123",
            username="testcreate",
        )

        initial_count = UserSubscription.objects.filter(user=user).count()
        self.assertEqual(initial_count, 3)

        # An auto-subscribe category, so the account WOULD join it if the
        # update were treated as a signup.
        new_default = SubscriptionTopic.objects.create(
            slug="new-default",
            name="New Default Topic",
            category=SubscriptionTopic.TopicCategory.ACCOUNT,
            is_active=True,
            is_default=True,
        )

        user.first_name = "Updated"
        user.save()

        final_count = UserSubscription.objects.filter(user=user).count()
        self.assertEqual(final_count, initial_count)

        self.assertFalse(
            UserSubscription.objects.filter(
                user=user, topic=new_default
            ).exists()
        )

    def test_inactive_default_topics_ignored(self):
        user = User.objects.create_user(
            email="inactivetest@test.com",
            password="testpass123",
            username="inactivetest",
        )

        self.assertFalse(
            UserSubscription.objects.filter(
                user=user, topic=self.inactive_default
            ).exists()
        )

    def test_bulk_create_users(self):
        users_data = [
            User(email=f"bulk{i}@test.com", username=f"bulk{i}")
            for i in range(3)
        ]

        created_users = User.objects.bulk_create(users_data)

        for user in created_users:
            self.assertEqual(
                UserSubscription.objects.filter(user=user).count(), 0
            )
