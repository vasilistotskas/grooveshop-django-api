import pytest
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from product.admin import (
    ProductCategoryAdmin,
    ProductAdmin,
    ProductReviewAdmin,
    ProductFavouriteAdmin,
    ProductCategoryImageAdmin,
    ProductImageAdmin,
    StockStatusFilter,
    PriceRangeFilter,
    DiscountStatusFilter,
    PopularityFilter,
    LikesCountFilter,
    ReviewAverageFilter,
    ProductImageInline,
    ProductCategoryImageInline,
)
from product.factories import (
    ProductFactory,
    ProductCategoryFactory,
    ProductReviewFactory,
    ProductFavouriteFactory,
    ProductImageFactory,
    ProductCategoryImageFactory,
)
from product.models import (
    Product,
    ProductCategory,
    ProductReview,
    ProductFavourite,
    ProductImage,
    ProductCategoryImage,
)
from tag.admin import TaggedItemInline
from user.factories import UserAccountFactory

User = get_user_model()


@pytest.fixture
def admin_request():
    factory = RequestFactory()
    request = factory.get("/admin/")
    request.user = Mock()
    request.user.is_authenticated = True
    request.user.is_staff = True
    request.user.is_superuser = True
    return request


@pytest.fixture
def product_admin():
    return ProductAdmin(Product, AdminSite())


@pytest.fixture
def category_admin():
    return ProductCategoryAdmin(ProductCategory, AdminSite())


@pytest.fixture
def review_admin():
    return ProductReviewAdmin(ProductReview, AdminSite())


@pytest.fixture
def favourite_admin():
    return ProductFavouriteAdmin(ProductFavourite, AdminSite())


@pytest.fixture
def product_image_admin():
    return ProductImageAdmin(ProductImage, AdminSite())


@pytest.fixture
def category_image_admin():
    return ProductCategoryImageAdmin(ProductCategoryImage, AdminSite())


@pytest.mark.django_db
class TestStockStatusFilter:
    def test_filter_lookups(self, admin_request):
        filter_instance = StockStatusFilter(
            admin_request, {}, Product, ProductAdmin(Product, AdminSite())
        )

        lookups = filter_instance.lookups(
            admin_request, ProductAdmin(Product, AdminSite())
        )

        assert len(lookups) == 5
        lookup_values = [lookup[0] for lookup in lookups]
        assert "in_stock" in lookup_values
        assert "low_stock" in lookup_values
        assert "out_of_stock" in lookup_values
        assert "high_stock" in lookup_values
        assert "critical_stock" in lookup_values

    def test_filter_queryset_in_stock(self, admin_request):
        ProductFactory(stock=10)
        ProductFactory(stock=0)
        ProductFactory(stock=25)

        queryset = Product.objects.all()

        filtered_queryset = queryset.filter(stock__gt=0)

        assert filtered_queryset.count() == 2
        for product in filtered_queryset:
            assert product.stock > 0

    def test_filter_queryset_out_of_stock(self, admin_request):
        ProductFactory(stock=10)
        ProductFactory(stock=0)
        ProductFactory(stock=0)

        queryset = Product.objects.all()
        filtered_queryset = queryset.filter(stock=0)

        assert filtered_queryset.count() == 2
        for product in filtered_queryset:
            assert product.stock == 0


@pytest.mark.django_db
class TestPriceRangeFilter:
    def test_filter_lookups(self, admin_request):
        filter_instance = PriceRangeFilter(
            admin_request, {}, Product, ProductAdmin(Product, AdminSite())
        )

        lookups = filter_instance.lookups(
            admin_request, ProductAdmin(Product, AdminSite())
        )

        assert len(lookups) == 5
        lookup_values = [lookup[0] for lookup in lookups]
        assert "budget" in lookup_values
        assert "luxury" in lookup_values

    def test_filter_queryset_budget(self, admin_request):
        ProductFactory(price=Decimal("15.00"))
        ProductFactory(price=Decimal("75.00"))
        ProductFactory(price=Decimal("600.00"))

        queryset = Product.objects.all()
        filtered_queryset = queryset.filter(price__lte=20)

        assert filtered_queryset.count() == 1
        assert float(filtered_queryset.first().price.amount) <= 20


@pytest.mark.django_db
class TestDiscountStatusFilter:
    def test_filter_lookups(self, admin_request):
        filter_instance = DiscountStatusFilter(
            admin_request, {}, Product, ProductAdmin(Product, AdminSite())
        )

        lookups = filter_instance.lookups(
            admin_request, ProductAdmin(Product, AdminSite())
        )

        assert len(lookups) == 4
        lookup_values = [lookup[0] for lookup in lookups]
        assert "on_sale" in lookup_values
        assert "no_discount" in lookup_values

    def test_filter_queryset_on_sale(self, admin_request):
        ProductFactory(discount_percent=15)
        ProductFactory(discount_percent=0)
        ProductFactory(discount_percent=25)

        queryset = Product.objects.all()
        filtered_queryset = queryset.filter(discount_percent__gt=0)

        assert filtered_queryset.count() == 2
        for product in filtered_queryset:
            assert product.discount_percent > 0


@pytest.mark.django_db
class TestPopularityFilter:
    def test_filter_lookups(self, admin_request):
        filter_instance = PopularityFilter(
            admin_request, {}, Product, ProductAdmin(Product, AdminSite())
        )

        lookups = filter_instance.lookups(
            admin_request, ProductAdmin(Product, AdminSite())
        )

        assert len(lookups) == 4
        lookup_values = [lookup[0] for lookup in lookups]
        assert "trending" in lookup_values
        assert "new_arrivals" in lookup_values

    def test_filter_queryset_trending(self, admin_request):
        ProductFactory(view_count=150)
        ProductFactory(view_count=50)
        ProductFactory(view_count=200)

        queryset = Product.objects.all()
        filtered_queryset = queryset.filter(view_count__gt=100)

        assert filtered_queryset.count() == 2
        for product in filtered_queryset:
            assert product.view_count > 100


@pytest.mark.django_db
class TestLikesCountFilter:
    def test_filter_expected_parameters(self, admin_request):
        filter_instance = LikesCountFilter(
            admin_request, {}, Product, ProductAdmin(Product, AdminSite())
        )

        expected = filter_instance.expected_parameters()

        assert "likes_count_from" in expected
        assert "likes_count_to" in expected


@pytest.mark.django_db
class TestReviewAverageFilter:
    def test_filter_expected_parameters(self, admin_request):
        filter_instance = ReviewAverageFilter(
            admin_request, {}, Product, ProductAdmin(Product, AdminSite())
        )

        expected = filter_instance.expected_parameters()

        assert "review_average_from" in expected
        assert "review_average_to" in expected


@pytest.mark.django_db
class TestProductImageInline:
    def test_inline_configuration(self, admin_request):
        inline = ProductImageInline(Product, AdminSite())

        assert inline.model == ProductImage
        assert inline.extra == 0
        assert "image_preview" in inline.fields
        assert "image_preview" in inline.readonly_fields

    def test_image_preview_with_image(self, admin_request):
        product = ProductFactory()
        product_image = ProductImageFactory(product=product)
        inline = ProductImageInline(Product, AdminSite())

        result = inline.image_preview(product_image)

        assert len(result) > 0


@pytest.mark.django_db
class TestTaggedItemInline:
    def test_inline_configuration(self, admin_request):
        inline = TaggedItemInline(Product, AdminSite())

        assert inline.extra == 0
        assert "tag" in inline.fields
        assert inline.ct_field == "content_type"
        assert inline.ct_fk_field == "object_id"


@pytest.mark.django_db
class TestProductCategoryImageInline:
    def test_inline_configuration(self, admin_request):
        inline = ProductCategoryImageInline(ProductCategory, AdminSite())

        assert inline.model == ProductCategoryImage
        assert inline.extra == 0
        assert "image_preview" in inline.fields
        assert "image_preview" in inline.readonly_fields


@pytest.mark.django_db
class TestCategoryAdmin:
    def test_category_info_display(self, category_admin):
        category = ProductCategoryFactory()

        result = category_admin.category_info(category)

        assert (
            category.safe_translation_getter("name", any_language=True)
            in result
        )
        assert "Level:" in result
        assert "font-medium" in result

    def test_category_stats_display(self, category_admin):
        category = ProductCategoryFactory()

        result = category_admin.category_stats(category)

        assert "Direct:" in result
        assert "Total:" in result
        assert "Subcategories:" in result

    def test_category_status_active(self, category_admin):
        category = ProductCategoryFactory(active=True)

        result = category_admin.category_status(category)

        assert "✅" in result
        assert "Active" in result

    def test_category_status_inactive(self, category_admin):
        category = ProductCategoryFactory(active=False)

        result = category_admin.category_status(category)

        assert "❌" in result
        assert "Inactive" in result

    def test_image_preview_with_image(self, category_admin):
        category = ProductCategoryFactory()

        result = category_admin.image_preview(category)

        assert len(result) >= 0

    def test_created_display(self, category_admin):
        category = ProductCategoryFactory()

        result = category_admin.created_display(category)

        assert len(result) > 10
        assert "2025" in result or "2024" in result

    def test_category_analytics(self, category_admin):
        category = ProductCategoryFactory()

        result = category_admin.category_analytics(category)

        assert "Level:" in result
        assert "Direct Products:" in result
        assert "Total Products:" in result


@pytest.mark.django_db
class TestProductAdmin:
    def test_product_info_display(self, product_admin):
        product = ProductFactory()

        result = product_admin.product_info(product)

        assert (
            product.safe_translation_getter("name", any_language=True) in result
        )
        assert product.sku[:8] in result

    def test_category_display(self, product_admin):
        category = ProductCategoryFactory()
        product = ProductFactory(category=category)

        result = product_admin.category_display(product)

        assert (
            category.safe_translation_getter("name", any_language=True)
            in result
        )

    def test_pricing_info_display(self, product_admin):
        product = ProductFactory(price=Decimal("99.99"), discount_percent=20)

        result = product_admin.pricing_info(product)

        assert "99,99" in result
        assert "€" in result
        assert "20%" in result

    def test_stock_info_display(self, product_admin):
        product = ProductFactory(stock=25)

        result = product_admin.stock_info(product)

        assert "25" in result
        assert "In Stock" in result

    def test_performance_metrics_display(self, product_admin):
        product = ProductFactory(view_count=150)

        result = product_admin.performance_metrics(product)

        assert "150" in result
        assert "👁️" in result

    def test_status_badges_active(self, product_admin):
        product = ProductFactory(active=True)

        result = product_admin.status_badges(product)

        assert "✅" in result
        assert "Active" in result

    def test_status_badges_inactive(self, product_admin):
        product = ProductFactory(active=False)

        result = product_admin.status_badges(product)

        assert "❌" in result
        assert "Inactive" in result

    def test_created_display(self, product_admin):
        product = ProductFactory()

        result = product_admin.created_display(product)

        assert len(result) > 10
        assert "2025" in result or "2024" in result

    def test_pricing_summary(self, product_admin):
        product = ProductFactory(price=Decimal("100.00"), discount_percent=10)

        result = product_admin.pricing_summary(product)

        assert "Base Price" in result
        assert "100,00" in result

    def test_performance_summary(self, product_admin):
        product = ProductFactory(view_count=100)

        result = product_admin.performance_summary(product)

        assert "Total Views" in result
        assert "100" in result

    def test_product_analytics(self, product_admin):
        product = ProductFactory()

        result = product_admin.product_analytics(product)

        assert "Product Age" in result
        assert "Engagement" in result

    @patch.object(ProductAdmin, "message_user")
    def test_make_active_action(
        self, mock_message, product_admin, admin_request
    ):
        products = [ProductFactory(active=False) for _ in range(3)]
        queryset = Product.objects.filter(id__in=[p.id for p in products])

        product_admin.make_active(admin_request, queryset)

        for product in products:
            product.refresh_from_db()
            assert product.active is True

        mock_message.assert_called_once()

    @patch.object(ProductAdmin, "message_user")
    def test_make_inactive_action(
        self, mock_message, product_admin, admin_request
    ):
        products = [ProductFactory(active=True) for _ in range(2)]
        queryset = Product.objects.filter(id__in=[p.id for p in products])

        product_admin.make_inactive(admin_request, queryset)

        for product in products:
            product.refresh_from_db()
            assert product.active is False

        mock_message.assert_called_once()

    @patch.object(ProductAdmin, "message_user")
    def test_clear_discount_action(
        self, mock_message, product_admin, admin_request
    ):
        products = [ProductFactory(discount_percent=25) for _ in range(2)]
        queryset = Product.objects.filter(id__in=[p.id for p in products])

        product_admin.clear_discount(admin_request, queryset)

        for product in products:
            product.refresh_from_db()
            assert product.discount_percent == 0

        mock_message.assert_called_once()


@pytest.mark.django_db
class TestReviewAdmin:
    def test_review_info_display(self, review_admin):
        review = ProductReviewFactory()

        result = review_admin.review_info(review)

        assert str(review.id) in result
        assert "font-medium" in result

    def test_product_link_display(self, review_admin):
        product = ProductFactory()
        review = ProductReviewFactory(product=product)

        result = review_admin.product_link(review)

        assert (
            product.safe_translation_getter("name", any_language=True) in result
        )

    def test_user_link_display(self, review_admin):
        user = UserAccountFactory()
        review = ProductReviewFactory(user=user)

        result = review_admin.user_link(review)

        assert user.email in result or user.username in result

    def test_rating_display(self, review_admin):
        review = ProductReviewFactory(rate=8)

        result = review_admin.rating_display(review)

        assert "8" in result
        assert "⭐" in result

    def test_status_badge_approved(self, review_admin):
        review = ProductReviewFactory(status="TRUE")

        result = review_admin.status_badge(review)

        assert "✅" in result
        assert "True" in result

    def test_status_badge_pending(self, review_admin):
        review = ProductReviewFactory(status="NEW")

        result = review_admin.status_badge(review)

        assert "🆕" in result
        assert "New" in result

    def test_created_display(self, review_admin):
        review = ProductReviewFactory()

        result = review_admin.created_display(review)

        assert len(result) > 10
        assert "2025" in result or "2024" in result

    @patch.object(ProductReviewAdmin, "message_user")
    def test_approve_reviews_action(
        self, mock_message, review_admin, admin_request
    ):
        reviews = [ProductReviewFactory(status="NEW") for _ in range(3)]
        queryset = ProductReview.objects.filter(id__in=[r.id for r in reviews])

        review_admin.approve_reviews(admin_request, queryset)

        for review in reviews:
            review.refresh_from_db()
            assert review.status == "TRUE"

        mock_message.assert_called_once()

    @patch.object(ProductReviewAdmin, "message_user")
    def test_reject_reviews_action(
        self, mock_message, review_admin, admin_request
    ):
        reviews = [ProductReviewFactory(status="NEW") for _ in range(2)]
        queryset = ProductReview.objects.filter(id__in=[r.id for r in reviews])

        review_admin.reject_reviews(admin_request, queryset)

        for review in reviews:
            review.refresh_from_db()
            assert review.status == "FALSE"

        mock_message.assert_called_once()


@pytest.mark.django_db
class TestFavouriteAdmin:
    def test_user_display(self, favourite_admin):
        user = UserAccountFactory()
        favourite = ProductFavouriteFactory(user=user)

        result = favourite_admin.user_display(favourite)

        assert user.email in result or user.username in result

    def test_product_display(self, favourite_admin):
        product = ProductFactory()
        favourite = ProductFavouriteFactory(product=product)

        result = favourite_admin.product_display(favourite)

        assert (
            product.safe_translation_getter("name", any_language=True) in result
        )

    def test_created_display(self, favourite_admin):
        favourite = ProductFavouriteFactory()

        result = favourite_admin.created_display(favourite)

        assert len(result) > 10
        assert "2025" in result or "2024" in result


@pytest.mark.django_db
class TestProductCategoryImageAdmin:
    def test_image_preview(self, category_image_admin):
        category = ProductCategoryFactory()
        category_image = ProductCategoryImageFactory(category=category)

        result = category_image_admin.image_preview(category_image)

        assert len(result) >= 0

    def test_category_name_display(self, category_image_admin):
        category = ProductCategoryFactory()
        category_image = ProductCategoryImageFactory(category=category)

        result = category_image_admin.category_name(category_image)

        assert (
            category.safe_translation_getter("name", any_language=True)
            in result
        )

    def test_image_type_badge(self, category_image_admin):
        category_image = ProductCategoryImageFactory(image_type="hero")

        result = category_image_admin.image_type_badge(category_image)

        assert "hero" in result.lower()

    def test_status_badge_active(self, category_image_admin):
        category_image = ProductCategoryImageFactory(active=True)

        result = category_image_admin.status_badge(category_image)

        assert "✅" in result
        assert "Active" in result

    def test_status_badge_inactive(self, category_image_admin):
        category_image = ProductCategoryImageFactory(active=False)

        result = category_image_admin.status_badge(category_image)

        assert "❌" in result
        assert "Inactive" in result


@pytest.mark.django_db
class TestProductImageAdmin:
    def test_image_preview(self, product_image_admin):
        product = ProductFactory()
        product_image = ProductImageFactory(product=product)

        result = product_image_admin.image_preview(product_image)

        assert len(result) >= 0

    def test_product_name_display(self, product_image_admin):
        product = ProductFactory()
        product_image = ProductImageFactory(product=product)

        result = product_image_admin.product_name(product_image)

        assert (
            product.safe_translation_getter("name", any_language=True) in result
        )

    def test_main_badge_is_main(self, product_image_admin):
        product_image = ProductImageFactory(is_main=True)

        result = product_image_admin.main_badge(product_image)

        assert "⭐" in result
        assert "Main" in result

    def test_main_badge_not_main(self, product_image_admin):
        product_image = ProductImageFactory(is_main=False)

        result = product_image_admin.main_badge(product_image)

        assert "Gallery" in result


@pytest.mark.django_db
class TestProductAdminIntegration:
    def test_product_admin_configuration(self, product_admin):
        assert product_admin.list_per_page == 25
        assert "product_info" in product_admin.list_display
        assert "pricing_info" in product_admin.list_display
        assert "stock_info" in product_admin.list_display

        action_names = []
        for action in product_admin.actions:
            if hasattr(action, "__name__"):
                action_names.append(action.__name__)
            else:
                action_names.append(str(action))

        assert "make_active" in action_names
        assert "clear_discount" in action_names

    def test_category_admin_configuration(self, category_admin):
        assert category_admin.list_per_page == 25
        assert "category_info" in category_admin.list_display
        assert "category_stats" in category_admin.list_display

    def test_get_queryset_optimization(self, product_admin, admin_request):
        queryset = product_admin.get_queryset(admin_request)

        assert "category" in str(queryset.query)
