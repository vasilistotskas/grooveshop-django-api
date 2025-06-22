from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from user.models.subscription import SubscriptionTopic, UserSubscription
from user.serializers.subscription import (
    BulkSubscriptionSerializer,
    SubscriptionTopicSerializer,
    UserSubscriptionSerializer,
    UserSubscriptionStatusSerializer,
)

User = get_user_model()


class SubscriptionTopicSerializerTest(TestCase):
    def setUp(self):
        self.topic = SubscriptionTopic.objects.create(
            slug="test-newsletter",
            name="Test Newsletter",
            description="A test newsletter",
            category=SubscriptionTopic.TopicCategory.NEWSLETTER,
            is_active=True,
            is_default=True,
            requires_confirmation=False,
        )

        self.users = []
        for i in range(3):
            user, _ = User.objects.get_or_create(
                email=f"user{i}@test.com", defaults={"password": "pass"}
            )
            self.users.append(user)

        UserSubscription.objects.filter(
            user__in=self.users, topic=self.topic
        ).delete()

        UserSubscription.objects.create(
            user=self.users[0],
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )
        UserSubscription.objects.create(
            user=self.users[1],
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        UserSubscription.objects.create(
            user=self.users[2],
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
        )

    def test_serializer_fields(self):
        serializer = SubscriptionTopicSerializer(self.topic)
        data = serializer.data

        expected_fields = {
            "id",
            "uuid",
            "slug",
            "category",
            "is_active",
            "is_default",
            "requires_confirmation",
            "subscriber_count",
            "translations",
        }

        self.assertEqual(set(data.keys()), expected_fields)

    def test_subscriber_count(self):
        serializer = SubscriptionTopicSerializer(self.topic)
        data = serializer.data

        self.assertEqual(data["subscriber_count"], 2)

    def test_read_only_fields(self):
        data = {
            "slug": "updated-slug",
            "name": "Updated Name",
            "subscriber_count": 999,
            "uuid": "new-uuid",
        }

        serializer = SubscriptionTopicSerializer(
            self.topic, data=data, partial=True
        )
        self.assertTrue(serializer.is_valid())

        updated_topic = serializer.save()
        self.assertEqual(updated_topic.slug, "updated-slug")
        self.assertNotEqual(updated_topic.uuid, "new-uuid")

    def test_multiple_topics_serialization(self):
        SubscriptionTopic.objects.create(
            slug="another-topic",
            name="Another Topic",
            category=SubscriptionTopic.TopicCategory.PRODUCT,
        )

        topics = SubscriptionTopic.objects.all()
        serializer = SubscriptionTopicSerializer(topics, many=True)

        self.assertEqual(len(serializer.data), 2)
        topics_by_slug = {item["slug"]: item for item in serializer.data}
        self.assertIn("test-newsletter", topics_by_slug)
        self.assertIn("another-topic", topics_by_slug)


class UserSubscriptionSerializerTest(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = User.objects.create_user(
            email="test@example.com", password="testpass123"
        )
        self.topic = SubscriptionTopic.objects.create(
            slug="test-topic",
            name="Test Topic",
            category=SubscriptionTopic.TopicCategory.NEWSLETTER,
            is_active=True,
        )

        self.inactive_topic = SubscriptionTopic.objects.create(
            slug="inactive-topic",
            name="Inactive Topic",
            category=SubscriptionTopic.TopicCategory.OTHER,
            is_active=False,
        )

    def test_serializer_fields(self):
        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        serializer = UserSubscriptionSerializer(subscription)
        data = serializer.data

        expected_fields = {
            "id",
            "user",
            "topic",
            "topic_details",
            "status",
            "subscribed_at",
            "unsubscribed_at",
            "metadata",
            "created_at",
            "updated_at",
        }

        self.assertEqual(set(data.keys()), expected_fields)
        self.assertIsNotNone(data["topic_details"])
        self.assertEqual(data["topic_details"]["slug"], "test-topic")

    def test_create_subscription(self):
        request = self.factory.post("/")
        request.user = self.user

        data = {
            "topic": self.topic.id,
            "status": UserSubscription.SubscriptionStatus.ACTIVE,
        }

        serializer = UserSubscriptionSerializer(
            data=data, context={"request": request}
        )
        self.assertTrue(serializer.is_valid())

        subscription = serializer.save(user=self.user)
        self.assertEqual(subscription.user, self.user)
        self.assertEqual(subscription.topic, self.topic)
        self.assertEqual(
            subscription.status, UserSubscription.SubscriptionStatus.ACTIVE
        )

    def test_validate_inactive_topic(self):
        request = self.factory.post("/")
        request.user = self.user

        data = {
            "topic": self.inactive_topic.id,
            "status": UserSubscription.SubscriptionStatus.ACTIVE,
        }

        serializer = UserSubscriptionSerializer(
            data=data, context={"request": request}
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("topic", serializer.errors)

        self.assertTrue(len(serializer.errors["topic"]) > 0)

    def test_validate_duplicate_subscription(self):
        UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        request = self.factory.post("/")
        request.user = self.user

        data = {
            "topic": self.topic.id,
            "status": UserSubscription.SubscriptionStatus.ACTIVE,
        }

        serializer = UserSubscriptionSerializer(
            data=data, context={"request": request}
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("topic", serializer.errors)
        self.assertIn("already subscribed", str(serializer.errors["topic"]))

    def test_validate_pending_subscription(self):
        UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
        )

        request = self.factory.post("/")
        request.user = self.user

        data = {
            "topic": self.topic.id,
            "status": UserSubscription.SubscriptionStatus.ACTIVE,
        }

        serializer = UserSubscriptionSerializer(
            data=data, context={"request": request}
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("topic", serializer.errors)
        self.assertIn("pending confirmation", str(serializer.errors["topic"]))

    def test_update_subscription_metadata(self):
        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        new_metadata = {"frequency": "daily", "language": "en"}

        serializer = UserSubscriptionSerializer(
            subscription, data={"metadata": new_metadata}, partial=True
        )
        self.assertTrue(serializer.is_valid())

        updated_subscription = serializer.save()
        self.assertEqual(updated_subscription.metadata, new_metadata)

    def test_read_only_fields_not_updated(self):
        subscription = UserSubscription.objects.create(
            user=self.user,
            topic=self.topic,
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )

        original_created_at = subscription.created_at

        serializer = UserSubscriptionSerializer(
            subscription,
            data={
                "created_at": "2020-01-01T00:00:00Z",
                "subscribed_at": "2020-01-01T00:00:00Z",
                "topic_details": {"name": "Changed"},
            },
            partial=True,
        )
        self.assertTrue(serializer.is_valid())

        updated_subscription = serializer.save()
        self.assertEqual(updated_subscription.created_at, original_created_at)


class BulkSubscriptionSerializerTest(TestCase):
    def setUp(self):
        self.topics = []
        for i in range(5):
            topic = SubscriptionTopic.objects.create(
                slug=f"topic-{i}",
                name=f"Topic {i}",
                category=SubscriptionTopic.TopicCategory.NEWSLETTER,
                is_active=i < 3,
            )
            self.topics.append(topic)

    def test_valid_bulk_subscribe(self):
        topic_ids = [self.topics[0].id, self.topics[1].id, self.topics[2].id]

        data = {"topic_ids": topic_ids, "action": "subscribe"}

        serializer = BulkSubscriptionSerializer(data=data)
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data["topic_ids"], topic_ids)
        self.assertEqual(serializer.validated_data["action"], "subscribe")

    def test_valid_bulk_unsubscribe(self):
        topic_ids = [self.topics[0].id, self.topics[1].id]

        data = {"topic_ids": topic_ids, "action": "unsubscribe"}

        serializer = BulkSubscriptionSerializer(data=data)
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data["action"], "unsubscribe")

    def test_invalid_action(self):
        data = {"topic_ids": [self.topics[0].id], "action": "invalid_action"}

        serializer = BulkSubscriptionSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("action", serializer.errors)

    def test_empty_topic_ids(self):
        data = {"topic_ids": [], "action": "subscribe"}

        serializer = BulkSubscriptionSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("topic_ids", serializer.errors)

    def test_inactive_topic_ids(self):
        topic_ids = [self.topics[0].id, self.topics[3].id]

        data = {"topic_ids": topic_ids, "action": "subscribe"}

        serializer = BulkSubscriptionSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("topic_ids", serializer.errors)
        self.assertIn(
            str(self.topics[3].id), str(serializer.errors["topic_ids"])
        )

    def test_non_existent_topic_ids(self):
        topic_ids = [self.topics[0].id, 99999]

        data = {"topic_ids": topic_ids, "action": "subscribe"}

        serializer = BulkSubscriptionSerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("topic_ids", serializer.errors)
        self.assertIn("99999", str(serializer.errors["topic_ids"]))

    def test_duplicate_topic_ids_handled(self):
        topic_ids = [self.topics[0].id, self.topics[0].id, self.topics[1].id]

        data = {"topic_ids": topic_ids, "action": "subscribe"}

        serializer = BulkSubscriptionSerializer(data=data)
        self.assertTrue(serializer.is_valid())
        self.assertEqual(len(serializer.validated_data["topic_ids"]), 3)


class UserSubscriptionStatusSerializerTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="test@example.com", password="testpass123"
        )

        self.subscribed_topics = []
        self.available_topics = []

        for i in range(3):
            topic = SubscriptionTopic.objects.create(
                slug=f"subscribed-{i}",
                name=f"Subscribed Topic {i}",
                category=SubscriptionTopic.TopicCategory.NEWSLETTER,
            )
            self.subscribed_topics.append(topic)

            UserSubscription.objects.create(
                user=self.user,
                topic=topic,
                status=UserSubscription.SubscriptionStatus.ACTIVE,
            )

        for i in range(2):
            topic = SubscriptionTopic.objects.create(
                slug=f"available-{i}",
                name=f"Available Topic {i}",
                category=SubscriptionTopic.TopicCategory.PRODUCT,
            )
            self.available_topics.append(topic)

    def test_serializer_structure(self):
        class MockSubscriptionStatus:
            def __init__(self, subscribed_topics, available_topics):
                self.subscribed = subscribed_topics
                self.available = available_topics

        mock_data = MockSubscriptionStatus(
            subscribed_topics=self.subscribed_topics,
            available_topics=self.available_topics,
        )

        serializer = UserSubscriptionStatusSerializer(mock_data)
        data = serializer.data

        self.assertIn("subscribed", data)
        self.assertIn("available", data)

        self.assertEqual(len(data["subscribed"]), 3)
        self.assertEqual(len(data["available"]), 2)

        for topic_data in data["subscribed"]:
            self.assertIn("id", topic_data)
            self.assertIn("slug", topic_data)
            self.assertIn("category", topic_data)

        for topic_data in data["available"]:
            self.assertIn("id", topic_data)
            self.assertIn("slug", topic_data)
            self.assertIn("category", topic_data)

    def test_read_only_fields(self):
        serializer = UserSubscriptionStatusSerializer()

        self.assertTrue(serializer.fields["subscribed"].read_only)
        self.assertTrue(serializer.fields["available"].read_only)
