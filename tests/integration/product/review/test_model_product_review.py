from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from product.models.product import Product
from product.models.review import ProductReview

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class ProductReviewModelTestCase(TestCase):
    user: User = None
    product: Product = None
    product_review: ProductReview = None

    def setUp(self):
        self.user = User.objects.create_user(
            email="test@test.com", password="test12345@!"
        )
        self.product = Product.objects.create(
            name="Sample Product",
            description="Sample Product Description",
            price=100.0,
            active=True,
            stock=10,
        )
        self.product_review = ProductReview.objects.create(
            product=self.product,
            user=self.user,
            rate=5,
            status="New",
        )
        for language in languages:
            self.product_review.set_current_language(language)
            self.product_review.comment = f"Sample Comment {language}"
            self.product_review.save()
        self.product_review.set_current_language(default_language)

    def test_fields(self):
        # Test if the fields are saved correctly
        self.assertEqual(self.product_review.product, self.product)
        self.assertEqual(self.product_review.user, self.user)
        self.assertEqual(self.product_review.rate, 5)
        self.assertEqual(self.product_review.status, "New")

    def test_verbose_names(self):
        self.assertEqual(
            ProductReview._meta.get_field("product").verbose_name, "product"
        )
        self.assertEqual(ProductReview._meta.get_field("user").verbose_name, "user")
        self.assertEqual(ProductReview._meta.get_field("rate").verbose_name, "Rate")
        self.assertEqual(ProductReview._meta.get_field("status").verbose_name, "Status")

    def test_meta_verbose_names(self):
        # Test verbose names from the Meta class
        self.assertEqual(ProductReview._meta.verbose_name, "Product Review")
        self.assertEqual(ProductReview._meta.verbose_name_plural, "Product Reviews")

    def test_unicode_representation(self):
        # Test the __unicode__ method returns the translated name
        self.assertEqual(
            self.product_review.__unicode__(),
            self.product_review.safe_translation_getter("comment"),
        )

    def test_translations(self):
        # Test if translations are saved correctly
        for language in languages:
            self.product_review.set_current_language(language)
            self.assertEqual(self.product_review.comment, f"Sample Comment {language}")

    def tearDown(self) -> None:
        super().tearDown()
        self.user.delete()
        self.product.delete()
        self.product_review.delete()
