from django.test import TestCase

from contact.models import Feedback, FeedbackCategory


class TestFeedbackManager(TestCase):
    def setUp(self):
        Feedback.objects.create(
            rating=5,
            category=FeedbackCategory.WEBSITE,
            message="The website redesign looks fantastic, nice work.",
        )
        Feedback.objects.create(
            rating=5,
            category=FeedbackCategory.PRODUCTS,
            message="Products arrived quickly and matched the listing.",
        )
        Feedback.objects.create(
            rating=2,
            category=FeedbackCategory.DELIVERY,
            message="Delivery estimate was inaccurate for my order.",
        )

    def test_for_list_returns_queryset(self):
        queryset = Feedback.objects.for_list()
        self.assertEqual(queryset.count(), 3)

    def test_for_detail_returns_queryset(self):
        queryset = Feedback.objects.for_detail()
        self.assertEqual(queryset.count(), 3)

    def test_by_rating_manager_method(self):
        five_star = Feedback.objects.by_rating(5)
        self.assertEqual(five_star.count(), 2)

        two_star = Feedback.objects.by_rating(2)
        self.assertEqual(two_star.count(), 1)

    def test_by_category_manager_method(self):
        website = Feedback.objects.by_category(FeedbackCategory.WEBSITE)
        self.assertEqual(website.count(), 1)

    def test_by_rating_and_by_category_chaining(self):
        result = Feedback.objects.by_rating(5).by_category(
            FeedbackCategory.WEBSITE
        )
        self.assertEqual(result.count(), 1)

    def test_manager_returns_correct_queryset_type(self):
        queryset = Feedback.objects.get_queryset()
        self.assertTrue(hasattr(queryset, "by_rating"))
        self.assertTrue(hasattr(queryset, "by_category"))
