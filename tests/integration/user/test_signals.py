from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import post_save
from django.test import TestCase, TransactionTestCase

from user.models.subscription import SubscriptionTopic, UserSubscription
from user.signals import create_default_subscriptions

User = get_user_model()


class CreateDefaultSubscriptionsSignalTest(TransactionTestCase):
    def setUp(self):
        self.default_topics = []
        self.non_default_topics = []

        for i in range(3):
            topic = SubscriptionTopic.objects.create(
                slug=f"default-topic-{i}",
                name=f"Default Topic {i}",
                category=SubscriptionTopic.TopicCategory.NEWSLETTER,
                is_active=True,
                is_default=True,
            )
            self.default_topics.append(topic)

        for i in range(2):
            topic = SubscriptionTopic.objects.create(
                slug=f"non-default-topic-{i}",
                name=f"Non-Default Topic {i}",
                category=SubscriptionTopic.TopicCategory.PRODUCT,
                is_active=True,
                is_default=False,
            )
            self.non_default_topics.append(topic)

        self.inactive_default = SubscriptionTopic.objects.create(
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

        subscribed_topics = set(sub.topic for sub in subscriptions)
        expected_topics = set(self.default_topics)
        self.assertEqual(subscribed_topics, expected_topics)

        for subscription in subscriptions:
            self.assertEqual(
                subscription.status, UserSubscription.SubscriptionStatus.ACTIVE
            )

    def test_existing_user_no_subscriptions(self):
        post_save.disconnect(create_default_subscriptions, sender=User)

        user = User.objects.create_user(
            email="existing@test.com",
            password="testpass123",
            username="existing",
        )

        post_save.connect(create_default_subscriptions, sender=User)

        self.assertEqual(UserSubscription.objects.filter(user=user).count(), 0)

        user.first_name = "Updated"
        user.save()

        self.assertEqual(UserSubscription.objects.filter(user=user).count(), 0)

    def test_signal_only_on_create(self):
        user = User.objects.create_user(
            email="testcreate@test.com",
            password="testpass123",
            username="testcreate",
        )

        initial_count = UserSubscription.objects.filter(user=user).count()
        self.assertEqual(initial_count, 3)

        new_default = SubscriptionTopic.objects.create(
            slug="new-default",
            name="New Default Topic",
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

    def test_signal_handles_errors_gracefully(self):
        SubscriptionTopic.objects.create(
            slug="problematic",
            name="Problematic Topic",
            is_active=True,
            is_default=True,
        )

        User.objects.create_user(
            email="errortest@test.com",
            password="testpass123",
            username="errortest",
        )

        self.assertTrue(
            User.objects.filter(email="errortest@test.com").exists()
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

    def test_signal_with_transaction(self):
        with transaction.atomic():
            user = User.objects.create_user(
                email="transactiontest@test.com",
                password="testpass123",
                username="transactiontest",
            )

            subscriptions = UserSubscription.objects.filter(user=user)
            self.assertEqual(subscriptions.count(), 3)

        self.assertEqual(UserSubscription.objects.filter(user=user).count(), 3)


class SignalDisconnectionTest(TestCase):
    def setUp(self):
        self.default_topic = SubscriptionTopic.objects.create(
            slug="default-topic",
            name="Default Topic",
            is_active=True,
            is_default=True,
        )

    def test_user_creation_without_signal(self):
        post_save.disconnect(create_default_subscriptions, sender=User)

        try:
            user = User.objects.create_user(
                email="nosignal@test.com",
                password="testpass123",
                username="nosignal",
            )

            self.assertEqual(
                UserSubscription.objects.filter(user=user).count(), 0
            )

        finally:
            post_save.connect(create_default_subscriptions, sender=User)

    def test_manual_subscription_creation(self):
        post_save.disconnect(create_default_subscriptions, sender=User)

        try:
            user = User.objects.create_user(
                email="manual@test.com",
                password="testpass123",
                username="manual",
            )

            default_topics = SubscriptionTopic.objects.filter(
                is_active=True, is_default=True
            )

            for topic in default_topics:
                UserSubscription.objects.create(
                    user=user,
                    topic=topic,
                    status=UserSubscription.SubscriptionStatus.ACTIVE,
                )

            self.assertEqual(
                UserSubscription.objects.filter(user=user).count(),
                default_topics.count(),
            )

        finally:
            post_save.connect(create_default_subscriptions, sender=User)
