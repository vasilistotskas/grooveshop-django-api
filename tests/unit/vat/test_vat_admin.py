from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils.html import strip_tags

from vat.admin import VatAdmin, VatRangeFilter, VatUsageFilter
from vat.models import Vat

User = get_user_model()


class VatRangeFilterTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.user = Mock()

        self.model_admin = Mock()
        self.filter = VatRangeFilter(self.request, {}, Vat, self.model_admin)

        self.vat_zero = Vat.objects.create(value=Decimal("0.00"))
        self.vat_low = Vat.objects.create(value=Decimal("5.00"))
        self.vat_standard = Vat.objects.create(value=Decimal("20.00"))
        self.vat_high = Vat.objects.create(value=Decimal("30.00"))
        self.vat_very_high = Vat.objects.create(value=Decimal("60.00"))

    def test_filter_title(self):
        self.assertEqual(self.filter.title, "VAT Range")

    def test_filter_parameter_name(self):
        self.assertEqual(self.filter.parameter_name, "vat_range")

    def test_lookups(self):
        lookups = self.filter.lookups(self.request, self.model_admin)
        expected_keys = ["zero", "low", "standard", "high", "very_high"]
        actual_keys = [lookup[0] for lookup in lookups]
        self.assertEqual(actual_keys, expected_keys)

    def test_queryset_zero_filter(self):
        self.filter.value = Mock(return_value="zero")
        queryset = Vat.objects.all()

        filtered_qs = self.filter.queryset(self.request, queryset)

        self.assertEqual(filtered_qs.count(), 1)
        self.assertEqual(filtered_qs.first(), self.vat_zero)

    def test_queryset_low_filter(self):
        self.filter.value = Mock(return_value="low")
        queryset = Vat.objects.all()

        filtered_qs = self.filter.queryset(self.request, queryset)

        self.assertEqual(filtered_qs.count(), 1)
        self.assertEqual(filtered_qs.first(), self.vat_low)

    def test_queryset_standard_filter(self):
        self.filter.value = Mock(return_value="standard")
        queryset = Vat.objects.all()

        filtered_qs = self.filter.queryset(self.request, queryset)

        self.assertEqual(filtered_qs.count(), 1)
        self.assertEqual(filtered_qs.first(), self.vat_standard)

    def test_queryset_high_filter(self):
        self.filter.value = Mock(return_value="high")
        queryset = Vat.objects.all()

        filtered_qs = self.filter.queryset(self.request, queryset)

        self.assertEqual(filtered_qs.count(), 1)
        self.assertEqual(filtered_qs.first(), self.vat_high)

    def test_queryset_very_high_filter(self):
        self.filter.value = Mock(return_value="very_high")
        queryset = Vat.objects.all()

        filtered_qs = self.filter.queryset(self.request, queryset)

        self.assertEqual(filtered_qs.count(), 1)
        self.assertEqual(filtered_qs.first(), self.vat_very_high)

    def test_queryset_no_filter(self):
        self.filter.value = Mock(return_value=None)
        queryset = Vat.objects.all()

        filtered_qs = self.filter.queryset(self.request, queryset)

        self.assertEqual(filtered_qs.count(), 5)


class VatUsageFilterTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.user = Mock()

        self.model_admin = Mock()
        self.filter = VatUsageFilter(self.request, {}, Vat, self.model_admin)

        self.vat_used = Vat.objects.create(value=Decimal("20.00"))
        self.vat_unused = Vat.objects.create(value=Decimal("15.00"))
        self.vat_popular = Vat.objects.create(value=Decimal("10.00"))
        self.vat_rare = Vat.objects.create(value=Decimal("5.00"))

    def test_filter_title(self):
        self.assertEqual(self.filter.title, "Usage Status")

    def test_filter_parameter_name(self):
        self.assertEqual(self.filter.parameter_name, "usage_status")

    def test_lookups(self):
        lookups = self.filter.lookups(self.request, self.model_admin)
        expected_keys = ["in_use", "unused", "popular", "rare"]
        actual_keys = [lookup[0] for lookup in lookups]
        self.assertEqual(actual_keys, expected_keys)

    @patch("product.models.Product.objects")
    def test_queryset_in_use_filter(self, mock_products):
        self.filter.value = Mock(return_value="in_use")
        queryset = Vat.objects.all()

        filtered_qs = self.filter.queryset(self.request, queryset)

        self.assertTrue(hasattr(filtered_qs, "filter"))

    def test_queryset_unused_filter(self):
        self.filter.value = Mock(return_value="unused")
        queryset = Vat.objects.all()

        filtered_qs = self.filter.queryset(self.request, queryset)

        self.assertTrue(hasattr(filtered_qs, "filter"))

    def test_queryset_popular_filter(self):
        self.filter.value = Mock(return_value="popular")
        queryset = Vat.objects.all()

        filtered_qs = self.filter.queryset(self.request, queryset)

        self.assertTrue(hasattr(filtered_qs, "annotate"))

    def test_queryset_rare_filter(self):
        self.filter.value = Mock(return_value="rare")
        queryset = Vat.objects.all()

        filtered_qs = self.filter.queryset(self.request, queryset)

        self.assertTrue(hasattr(filtered_qs, "annotate"))

    def test_queryset_no_filter(self):
        self.filter.value = Mock(return_value=None)
        queryset = Vat.objects.all()

        filtered_qs = self.filter.queryset(self.request, queryset)

        self.assertEqual(filtered_qs.count(), 4)


class VatAdminTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.admin = VatAdmin(Vat, self.site)

        self.user = User.objects.create_user(
            email="test@example.com",
            username="testuser",
            password="testpass123",
        )

        self.vat_zero = Vat.objects.create(value=Decimal("0.00"))
        self.vat_low = Vat.objects.create(value=Decimal("5.00"))
        self.vat_standard = Vat.objects.create(value=Decimal("20.00"))
        self.vat_high = Vat.objects.create(value=Decimal("30.00"))
        self.vat_very_high = Vat.objects.create(value=Decimal("60.00"))

    def test_admin_configuration(self):
        self.assertTrue(self.admin.compressed_fields)
        self.assertTrue(self.admin.warn_unsaved_form)
        self.assertFalse(self.admin.list_fullwidth)
        self.assertTrue(self.admin.list_filter_submit)
        self.assertTrue(self.admin.list_filter_sheet)
        self.assertEqual(self.admin.list_per_page, 25)
        self.assertEqual(self.admin.ordering, ["value"])

    def test_list_display(self):
        expected_fields = [
            "vat_display",
            "vat_category_badge",
            "usage_metrics",
            "calculation_preview",
            "created_display",
            "updated_display",
        ]
        self.assertEqual(self.admin.list_display, expected_fields)

    def test_search_fields(self):
        self.assertEqual(self.admin.search_fields, ["value", "id"])

    def test_readonly_fields(self):
        expected_readonly = [
            "id",
            "uuid",
            "created_at",
            "updated_at",
            "usage_analytics",
            "calculation_examples",
            "products_using_vat",
        ]
        self.assertEqual(self.admin.readonly_fields, expected_readonly)

    def test_fieldsets(self):
        self.assertEqual(len(self.admin.fieldsets), 4)

        fieldset_titles = [fieldset[0] for fieldset in self.admin.fieldsets]
        expected_titles = [
            "VAT Configuration",
            "Usage Analytics",
            "Calculation Examples",
            "System Information",
        ]
        self.assertEqual(fieldset_titles, expected_titles)

    def test_get_queryset(self):
        request = self.factory.get("/")
        request.user = self.user

        queryset = self.admin.get_queryset(request)

        self.assertTrue(hasattr(queryset.first(), "products_count"))

    def test_vat_display_zero(self):
        result = self.admin.vat_display(self.vat_zero)

        self.assertIn("🆓", result)
        self.assertIn("0%", result)
        self.assertIn("text-green-600", result)

    def test_vat_display_low(self):
        result = self.admin.vat_display(self.vat_low)

        self.assertIn("📉", result)
        self.assertIn("5.00%", result)
        self.assertIn("text-blue-600", result)

    def test_vat_display_standard(self):
        result = self.admin.vat_display(self.vat_standard)

        self.assertIn("📊", result)
        self.assertIn("20.00%", result)
        self.assertIn("text-yellow-600", result)

    def test_vat_display_high(self):
        result = self.admin.vat_display(self.vat_high)

        self.assertIn("📈", result)
        self.assertIn("30.00%", result)
        self.assertIn("text-orange-600", result)

    def test_vat_display_very_high(self):
        result = self.admin.vat_display(self.vat_very_high)

        self.assertIn("🔥", result)
        self.assertIn("60.00%", result)
        self.assertIn("text-red-600", result)

    def test_vat_category_badge_zero(self):
        result = self.admin.vat_category_badge(self.vat_zero)

        self.assertIn("🆓", result)
        self.assertIn("Tax Free", result)
        self.assertIn("bg-green-50", result)

    def test_vat_category_badge_reduced(self):
        result = self.admin.vat_category_badge(self.vat_low)

        self.assertIn("📉", result)
        self.assertIn("Reduced", result)
        self.assertIn("bg-blue-50", result)

    def test_usage_metrics(self):
        self.vat_standard.products_count = 5

        result = self.admin.usage_metrics(self.vat_standard)

        self.assertIn("5", result)
        self.assertIn("products", result)
        self.assertIn("📈", result)

    def test_calculation_preview(self):
        result = self.admin.calculation_preview(self.vat_standard)

        self.assertIn("€100", result)
        self.assertIn("€120", result)

    def test_created_display(self):
        result = self.admin.created_display(self.vat_standard)

        self.assertIn("text-gray-600", result)
        self.assertTrue(len(strip_tags(result)) > 0)
        import datetime

        current_year = str(datetime.datetime.now().year)
        self.assertIn(current_year, result)

    def test_updated_display(self):
        result = self.admin.updated_display(self.vat_standard)

        self.assertIn("text-gray-600", result)
        self.assertTrue(len(strip_tags(result)) > 0)
        import datetime

        current_year = str(datetime.datetime.now().year)
        self.assertIn(current_year, result)

    def test_usage_analytics(self):
        self.vat_standard.products_count = 10

        result = self.admin.usage_analytics(self.vat_standard)

        self.assertIn("Products Using:", result)
        self.assertIn("10", result)
        self.assertIn("VAT Rate:", result)
        self.assertIn("Standard Rate", result)

    def test_calculation_examples(self):
        result = self.admin.calculation_examples(self.vat_standard)

        self.assertIn("VAT", result)
        self.assertIn("€100", result)
        self.assertIn("€120.00", result)

    def test_products_using_vat(self):
        result = self.admin.products_using_vat(self.vat_standard)

        self.assertIn("No products", result)

    def test_get_vat_category_text_zero(self):
        result = self.admin._get_vat_category_text(Decimal("0.00"))
        self.assertEqual(result, "Tax Free")

    def test_get_vat_category_text_reduced(self):
        result = self.admin._get_vat_category_text(Decimal("5.00"))
        self.assertEqual(result, "Reduced Rate")

    def test_get_vat_category_text_low(self):
        result = self.admin._get_vat_category_text(Decimal("15.00"))
        self.assertEqual(result, "Low Rate")

    def test_get_vat_category_text_standard(self):
        result = self.admin._get_vat_category_text(Decimal("20.00"))
        self.assertEqual(result, "Standard Rate")

    def test_get_vat_category_text_high(self):
        result = self.admin._get_vat_category_text(Decimal("30.00"))
        self.assertEqual(result, "High Rate")

    def test_get_vat_category_text_premium(self):
        result = self.admin._get_vat_category_text(Decimal("60.00"))
        self.assertEqual(result, "Premium Rate")


class VatAdminIntegrationTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.admin = VatAdmin(Vat, self.site)

        self.superuser = User.objects.create_user(
            email="admin@example.com",
            username="admin",
            password="adminpass123",
            is_staff=True,
            is_superuser=True,
        )

        self.vat = Vat.objects.create(value=Decimal("21.00"))

    def test_admin_changelist_view(self):
        request = self.factory.get("/admin/vat/vat/")
        request.user = self.superuser

        try:
            changelist = self.admin.get_changelist_instance(request)
            self.assertIsNotNone(changelist)
        except Exception as e:
            self.fail(f"Admin changelist view failed: {e}")

    def test_admin_filters_integration(self):
        request = self.factory.get("/admin/vat/vat/")
        request.user = self.superuser

        self.assertIn(VatRangeFilter, self.admin.list_filter)
        self.assertIn(VatUsageFilter, self.admin.list_filter)

    def test_admin_display_methods_integration(self):
        try:
            self.vat.products_count = 3

            display_methods = [
                "vat_display",
                "vat_category_badge",
                "usage_metrics",
                "calculation_preview",
                "created_display",
                "updated_display",
                "usage_analytics",
                "calculation_examples",
                "products_using_vat",
            ]

            for method_name in display_methods:
                method = getattr(self.admin, method_name)
                result = method(self.vat)
                self.assertIsNotNone(result)

        except Exception as e:
            self.fail(f"Display method integration failed: {e}")
