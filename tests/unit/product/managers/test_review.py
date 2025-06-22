from datetime import timedelta

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.utils import timezone

from product.enum.review import RateEnum, ReviewStatus
from product.factories import ProductFactory, ProductReviewFactory
from product.models.review import ProductReview
from user.factories import UserAccountFactory

User = get_user_model()


@pytest.fixture
def user():
    return UserAccountFactory()


@pytest.fixture
def product():
    return ProductFactory()


@pytest.fixture
def product_review(user, product):
    return ProductReviewFactory(
        user=user,
        product=product,
        rate=RateEnum.FIVE,
        status=ReviewStatus.TRUE,
        is_published=True,
    )


@pytest.mark.django_db
class TestEnhancedReviewQuerySet:
    def test_approved_reviews(self):
        approved_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            status=ReviewStatus.TRUE,
        )
        pending_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            status=ReviewStatus.NEW,
        )

        approved_reviews = ProductReview.objects.approved()

        assert approved_review in approved_reviews
        assert pending_review not in approved_reviews

    def test_pending_reviews(self):
        pending_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            status=ReviewStatus.NEW,
        )
        approved_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            status=ReviewStatus.TRUE,
        )

        pending_reviews = ProductReview.objects.pending()

        assert pending_review in pending_reviews
        assert approved_review not in pending_reviews

    def test_rejected_reviews(self):
        rejected_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            status=ReviewStatus.FALSE,
        )
        approved_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            status=ReviewStatus.TRUE,
        )

        rejected_reviews = ProductReview.objects.rejected()

        assert rejected_review in rejected_reviews
        assert approved_review not in rejected_reviews

    def test_published_reviews(self):
        published_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            is_published=True,
        )
        unpublished_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            is_published=False,
        )

        published_reviews = ProductReview.objects.published()

        assert published_review in published_reviews
        assert unpublished_review not in published_reviews

    def test_visible_reviews(self):
        visible_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            is_published=True,
        )
        hidden_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            is_published=False,
        )

        visible_reviews = ProductReview.objects.visible()

        assert visible_review in visible_reviews
        assert hidden_review not in visible_reviews

    def test_for_product_with_object(self, product):
        review1 = ProductReviewFactory(
            user=UserAccountFactory(), product=product
        )
        review2 = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )

        product_reviews = ProductReview.objects.for_product(product)

        assert review1 in product_reviews
        assert review2 not in product_reviews

    def test_for_product_with_id(self, product):
        review1 = ProductReviewFactory(
            user=UserAccountFactory(), product=product
        )
        review2 = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )

        product_reviews = ProductReview.objects.for_product(product.id)

        assert review1 in product_reviews
        assert review2 not in product_reviews

    def test_for_user_with_object(self, user):
        review1 = ProductReviewFactory(user=user, product=ProductFactory())
        review2 = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )

        user_reviews = ProductReview.objects.for_user(user)

        assert review1 in user_reviews
        assert review2 not in user_reviews

    def test_for_user_with_id(self, user):
        review1 = ProductReviewFactory(user=user, product=ProductFactory())
        review2 = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )

        user_reviews = ProductReview.objects.for_user(user.id)

        assert review1 in user_reviews
        assert review2 not in user_reviews

    def test_by_rate(self):
        five_star_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            rate=RateEnum.FIVE,
        )
        three_star_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            rate=RateEnum.THREE,
        )

        five_star_reviews = ProductReview.objects.by_rate(RateEnum.FIVE)

        assert five_star_review in five_star_reviews
        assert three_star_review not in five_star_reviews

    def test_high_rated_default_threshold(self):
        high_rated_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            rate=RateEnum.FIVE,
        )
        medium_rated_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            rate=RateEnum.THREE,
        )

        high_rated_reviews = ProductReview.objects.high_rated()

        assert high_rated_review in high_rated_reviews
        assert medium_rated_review not in high_rated_reviews

    def test_high_rated_custom_threshold(self):
        high_rated_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            rate=RateEnum.THREE,
        )
        low_rated_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            rate=RateEnum.ONE,
        )

        high_rated_reviews = ProductReview.objects.high_rated(min_rate=3)

        assert high_rated_review in high_rated_reviews
        assert low_rated_review not in high_rated_reviews

    def test_low_rated_default_threshold(self):
        low_rated_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            rate=RateEnum.ONE,
        )
        high_rated_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            rate=RateEnum.FIVE,
        )

        low_rated_reviews = ProductReview.objects.low_rated()

        assert low_rated_review in low_rated_reviews
        assert high_rated_review not in low_rated_reviews

    def test_low_rated_custom_threshold(self):
        low_rated_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            rate=RateEnum.THREE,
        )
        high_rated_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            rate=RateEnum.FIVE,
        )

        low_rated_reviews = ProductReview.objects.low_rated(max_rate=3)

        assert low_rated_review in low_rated_reviews
        assert high_rated_review not in low_rated_reviews

    def test_recent_default_days(self, product_review):
        old_review = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )
        old_review.created_at = timezone.now() - timedelta(days=35)
        old_review.save()

        recent_reviews = ProductReview.objects.recent()

        assert product_review in recent_reviews
        assert old_review not in recent_reviews

    def test_recent_custom_days(self, product_review):
        old_review = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )
        old_review.created_at = timezone.now() - timedelta(days=10)
        old_review.save()

        recent_reviews = ProductReview.objects.recent(days=7)

        assert product_review in recent_reviews
        assert old_review not in recent_reviews

    def test_with_comments(self):
        review_with_comment = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )

        ProductReviewTranslation = apps.get_model(
            "product", "ProductReviewTranslation"
        )

        review_without_comment = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )
        review_without_comment.translations.all().delete()
        ProductReviewTranslation.objects.create(
            master=review_without_comment, language_code="en", comment=None
        )

        reviews_with_comments = ProductReview.objects.with_comments()

        assert review_with_comment in reviews_with_comments
        assert review_without_comment not in reviews_with_comments

    def test_without_comments(self):
        review_with_comment = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )

        ProductReviewTranslation = apps.get_model(
            "product", "ProductReviewTranslation"
        )

        review_without_comment = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )
        review_without_comment.translations.all().delete()
        ProductReviewTranslation.objects.create(
            master=review_without_comment, language_code="en", comment=None
        )

        review_with_whitespace_comment = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )
        review_with_whitespace_comment.translations.all().delete()
        ProductReviewTranslation.objects.create(
            master=review_with_whitespace_comment,
            language_code="en",
            comment="   ",
        )

        reviews_without_comments = ProductReview.objects.without_comments()

        assert review_without_comment in reviews_without_comments
        assert review_with_comment not in reviews_without_comments
        assert review_with_whitespace_comment not in reviews_without_comments

    def test_with_product_details(self, product_review):
        queryset = ProductReview.objects.with_product_details()

        assert product_review in queryset

        query_str = str(queryset.query)
        assert "product" in query_str.lower()

    def test_annotate_user_review_count(self, user, product):
        ProductReviewFactory(user=user, product=product)
        ProductReviewFactory(user=user, product=ProductFactory())

        annotated_reviews = ProductReview.objects.annotate_user_review_count()
        review = annotated_reviews.filter(user=user).first()

        assert hasattr(review, "user_review_count")
        assert review.user_review_count >= 2

    def test_complex_comment_filtering(self):
        ProductReviewTranslation = apps.get_model(
            "product", "ProductReviewTranslation"
        )

        review_with_empty_comment = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )
        review_with_empty_comment.translations.all().delete()
        ProductReviewTranslation.objects.create(
            master=review_with_empty_comment, language_code="en", comment=""
        )

        review_with_whitespace_comment = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )
        review_with_whitespace_comment.translations.all().delete()
        ProductReviewTranslation.objects.create(
            master=review_with_whitespace_comment,
            language_code="en",
            comment="   ",
        )

        reviews_without_comments = ProductReview.objects.without_comments()

        assert review_with_empty_comment in reviews_without_comments
        assert review_with_whitespace_comment not in reviews_without_comments


@pytest.mark.django_db
class TestProductReviewManager:
    def test_manager_delegates_to_queryset_approved(self):
        approved_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            status=ReviewStatus.TRUE,
        )
        pending_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            status=ReviewStatus.NEW,
        )

        approved_reviews = ProductReview.objects.approved()

        assert approved_review in approved_reviews
        assert pending_review not in approved_reviews

    def test_manager_delegates_to_queryset_pending(self):
        pending_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            status=ReviewStatus.NEW,
        )
        approved_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            status=ReviewStatus.TRUE,
        )

        pending_reviews = ProductReview.objects.pending()

        assert pending_review in pending_reviews
        assert approved_review not in pending_reviews

    def test_manager_delegates_to_queryset_rejected(self):
        rejected_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            status=ReviewStatus.FALSE,
        )
        approved_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            status=ReviewStatus.TRUE,
        )

        rejected_reviews = ProductReview.objects.rejected()

        assert rejected_review in rejected_reviews
        assert approved_review not in rejected_reviews

    def test_manager_delegates_to_queryset_published(self):
        published_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            is_published=True,
        )
        unpublished_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            is_published=False,
        )

        published_reviews = ProductReview.objects.published()

        assert published_review in published_reviews
        assert unpublished_review not in published_reviews

    def test_manager_delegates_to_queryset_visible(self):
        visible_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            is_published=True,
        )
        hidden_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            is_published=False,
        )

        visible_reviews = ProductReview.objects.visible()

        assert visible_review in visible_reviews
        assert hidden_review not in visible_reviews

    def test_manager_delegates_to_queryset_for_product(self, product):
        review1 = ProductReviewFactory(
            user=UserAccountFactory(), product=product
        )
        review2 = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )

        product_reviews = ProductReview.objects.for_product(product)

        assert review1 in product_reviews
        assert review2 not in product_reviews

    def test_manager_delegates_to_queryset_for_user(self, user):
        review1 = ProductReviewFactory(user=user, product=ProductFactory())
        review2 = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )

        user_reviews = ProductReview.objects.for_user(user)

        assert review1 in user_reviews
        assert review2 not in user_reviews

    def test_manager_delegates_to_queryset_by_rate(self):
        five_star_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            rate=RateEnum.FIVE,
        )
        three_star_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            rate=RateEnum.THREE,
        )

        five_star_reviews = ProductReview.objects.by_rate(RateEnum.FIVE)

        assert five_star_review in five_star_reviews
        assert three_star_review not in five_star_reviews

    def test_manager_delegates_to_queryset_high_rated(self):
        high_rated_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            rate=RateEnum.FIVE,
        )
        low_rated_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            rate=RateEnum.ONE,
        )

        high_rated_reviews = ProductReview.objects.high_rated()

        assert high_rated_review in high_rated_reviews
        assert low_rated_review not in high_rated_reviews

    def test_manager_delegates_to_queryset_low_rated(self):
        low_rated_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            rate=RateEnum.ONE,
        )
        high_rated_review = ProductReviewFactory(
            user=UserAccountFactory(),
            product=ProductFactory(),
            rate=RateEnum.FIVE,
        )

        low_rated_reviews = ProductReview.objects.low_rated()

        assert low_rated_review in low_rated_reviews
        assert high_rated_review not in low_rated_reviews

    def test_manager_delegates_to_queryset_recent(self, product_review):
        old_review = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )
        old_review.created_at = timezone.now() - timedelta(days=35)
        old_review.save()

        recent_reviews = ProductReview.objects.recent()

        assert product_review in recent_reviews
        assert old_review not in recent_reviews

    def test_manager_delegates_to_queryset_with_comments(self):
        review_with_comment = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )

        ProductReviewTranslation = apps.get_model(
            "product", "ProductReviewTranslation"
        )

        review_without_comment = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )
        review_without_comment.translations.all().delete()
        ProductReviewTranslation.objects.create(
            master=review_without_comment, language_code="en", comment=None
        )

        reviews_with_comments = ProductReview.objects.with_comments()

        assert review_with_comment in reviews_with_comments
        assert review_without_comment not in reviews_with_comments

    def test_manager_delegates_to_queryset_without_comments(self):
        review_with_comment = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )

        ProductReviewTranslation = apps.get_model(
            "product", "ProductReviewTranslation"
        )

        review_without_comment = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )
        review_without_comment.translations.all().delete()
        ProductReviewTranslation.objects.create(
            master=review_without_comment, language_code="en", comment=None
        )

        review_with_whitespace_comment = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )
        review_with_whitespace_comment.translations.all().delete()
        ProductReviewTranslation.objects.create(
            master=review_with_whitespace_comment,
            language_code="en",
            comment="   ",
        )

        reviews_without_comments = ProductReview.objects.without_comments()

        assert review_without_comment in reviews_without_comments
        assert review_with_comment not in reviews_without_comments
        assert review_with_whitespace_comment not in reviews_without_comments

    def test_manager_delegates_to_queryset_with_product_details(
        self, product_review
    ):
        reviews_with_details = ProductReview.objects.with_product_details()
        review = reviews_with_details.get(id=product_review.id)

        assert hasattr(review, "product")
        assert hasattr(review, "user")

    def test_rate_boundary_conditions(self):
        min_rate_review = ProductReviewFactory(rate=RateEnum.ONE)
        max_rate_review = ProductReviewFactory(rate=RateEnum.TEN)

        high_rated_reviews = ProductReview.objects.high_rated(min_rate=1)
        low_rated_reviews = ProductReview.objects.low_rated(max_rate=10)

        assert min_rate_review in high_rated_reviews
        assert max_rate_review in high_rated_reviews
        assert min_rate_review in low_rated_reviews
        assert max_rate_review in low_rated_reviews

    def test_empty_queryset_methods(self):
        assert ProductReview.objects.approved().count() == 0
        assert ProductReview.objects.pending().count() == 0
        assert ProductReview.objects.rejected().count() == 0
        assert ProductReview.objects.published().count() == 0
        assert ProductReview.objects.visible().count() == 0

    def test_chained_filters(self, product_review):
        chained_reviews = (
            ProductReview.objects.approved()
            .published()
            .visible()
            .high_rated()
            .recent()
        )

        assert product_review in chained_reviews

    def test_multiple_products_and_users(self):
        user1 = UserAccountFactory()
        user2 = UserAccountFactory()
        product1 = ProductFactory()
        product2 = ProductFactory()

        review1 = ProductReviewFactory(user=user1, product=product1)
        review2 = ProductReviewFactory(user=user2, product=product1)
        review3 = ProductReviewFactory(user=user1, product=product2)

        product1_reviews = ProductReview.objects.for_product(product1)
        assert review1 in product1_reviews
        assert review2 in product1_reviews
        assert review3 not in product1_reviews

        user1_reviews = ProductReview.objects.for_user(user1)
        assert review1 in user1_reviews
        assert review3 in user1_reviews
        assert review2 not in user1_reviews

    def test_complex_comment_filtering(self):
        ProductReviewTranslation = apps.get_model(
            "product", "ProductReviewTranslation"
        )

        review_with_empty_comment = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )
        review_with_empty_comment.translations.all().delete()
        ProductReviewTranslation.objects.create(
            master=review_with_empty_comment, language_code="en", comment=""
        )

        review_with_whitespace_comment = ProductReviewFactory(
            user=UserAccountFactory(), product=ProductFactory()
        )
        review_with_whitespace_comment.translations.all().delete()
        ProductReviewTranslation.objects.create(
            master=review_with_whitespace_comment,
            language_code="en",
            comment="   ",
        )

        reviews_without_comments = ProductReview.objects.without_comments()

        assert review_with_empty_comment in reviews_without_comments
        assert review_with_whitespace_comment not in reviews_without_comments
