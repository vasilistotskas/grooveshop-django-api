from django.test import TestCase

from contact.factories import FeedbackFactory
from contact.models import Feedback, FeedbackCategory


class TestFeedbackFactory(TestCase):
    def test_feedback_factory_creates_feedback(self):
        feedback = FeedbackFactory()

        self.assertIsInstance(feedback, Feedback)
        self.assertIsNotNone(feedback.name)
        self.assertIsNotNone(feedback.email)
        self.assertIsNotNone(feedback.message)
        self.assertIn(feedback.rating, range(1, 6))
        self.assertIn(feedback.category, [c.value for c in FeedbackCategory])

        self.assertIsNotNone(feedback.id)
        retrieved = Feedback.objects.get(id=feedback.id)
        self.assertEqual(retrieved, feedback)

    def test_feedback_factory_with_custom_attributes(self):
        feedback = FeedbackFactory(
            name="Test Name",
            email="test@example.com",
            rating=1,
            category=FeedbackCategory.OTHER,
            message="Custom feedback message content here.",
        )

        self.assertEqual(feedback.name, "Test Name")
        self.assertEqual(feedback.email, "test@example.com")
        self.assertEqual(feedback.rating, 1)
        self.assertEqual(feedback.category, FeedbackCategory.OTHER)
        self.assertEqual(
            feedback.message, "Custom feedback message content here."
        )

    def test_feedback_factory_signal_muted(self):
        feedback = FeedbackFactory()

        self.assertIsNotNone(feedback.id)
        self.assertTrue(Feedback.objects.filter(id=feedback.id).exists())
