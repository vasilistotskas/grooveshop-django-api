from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory
from product.models.product import Product
from product.models.review import ProductReview
from user.factories.account import UserAccountFactory

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class ProductReviewModelTestCase(TestCase):
    user: User = None
    product: Product = None
    product_review: ProductReview = None

    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.product = ProductFactory(num_images=0, num_reviews=0)
        self.product_review = ProductReviewFactory(
            product=self.product,
            user=self.user,
            rate=5,
            status="New",
        )

    def test_fields(self):
        self.assertEqual(self.product_review.product, self.product)
        self.assertEqual(self.product_review.user, self.user)
        self.assertEqual(self.product_review.rate, 5)
        self.assertEqual(self.product_review.status, "New")

    def test_str_representation(self):
        comment_snippet = (
            (self.product_review.safe_translation_getter("comment", any_language=True)[:50] + "...")
            if self.product_review.comment
            else "No Comment"
        )
        self.assertEqual(
            str(self.product_review),
            f"Review by {self.user.email} on {self.product}: {comment_snippet}",
        )

    def test_unicode_representation(self):
        comment_snippet = (
            (self.product_review.safe_translation_getter("comment", any_language=True)[:50] + "...")
            if self.product_review.comment
            else "No Comment"
        )
        self.assertEqual(
            self.product_review.__unicode__(),
            f"Review by {self.user.email} on {self.product}: {comment_snippet}",
        )
