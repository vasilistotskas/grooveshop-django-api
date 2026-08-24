import pytest
from django.test import TestCase

from contact.models import Feedback, FeedbackCategory

pytestmark = pytest.mark.assert_english


class TestFeedbackModel(TestCase):
    def test_create_feedback_with_all_fields(self):
        feedback = Feedback.objects.create(
            name="John Doe",
            email="john@example.com",
            rating=5,
            category=FeedbackCategory.WEBSITE,
            message="The new checkout flow is excellent, well done.",
        )

        self.assertEqual(feedback.name, "John Doe")
        self.assertEqual(feedback.email, "john@example.com")
        self.assertEqual(feedback.rating, 5)
        self.assertEqual(feedback.category, FeedbackCategory.WEBSITE)
        self.assertIsNotNone(feedback.id)
        self.assertIsNotNone(feedback.uuid)
        self.assertIsNotNone(feedback.created_at)
        self.assertIsNotNone(feedback.updated_at)

    def test_create_anonymous_feedback(self):
        feedback = Feedback.objects.create(
            rating=3,
            message="It was an okay experience overall, nothing special.",
        )

        self.assertEqual(feedback.name, "")
        self.assertEqual(feedback.email, "")
        self.assertEqual(feedback.category, FeedbackCategory.GENERAL)

    def test_str_with_name(self):
        feedback = Feedback.objects.create(
            name="Jane Smith",
            rating=4,
            category=FeedbackCategory.PRODUCTS,
            message="Products are good quality for the price offered.",
        )

        expected = f"{feedback.get_category_display()} · 4★ · Jane Smith"
        self.assertEqual(str(feedback), expected)

    def test_str_without_name_is_anonymous(self):
        feedback = Feedback.objects.create(
            rating=2,
            category=FeedbackCategory.DELIVERY,
            message="Delivery was slower than the estimate provided.",
        )

        result = str(feedback)
        self.assertIn("2★", result)
        self.assertIn("Anonymous", result)

    def test_category_choices(self):
        values = {choice.value for choice in FeedbackCategory}
        self.assertEqual(
            values,
            {"general", "website", "products", "delivery", "support", "other"},
        )

    def test_default_ordering_is_newest_first(self):
        first = Feedback.objects.create(
            rating=1, message="First feedback message with enough length."
        )
        second = Feedback.objects.create(
            rating=2, message="Second feedback message with enough length."
        )

        ordered_ids = list(Feedback.objects.values_list("id", flat=True))
        self.assertEqual(ordered_ids, [second.id, first.id])
