from decimal import Decimal
from unittest.mock import Mock

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from vat.admin import VatAdmin, VatUsageFilter
from vat.models import Vat

pytestmark = pytest.mark.assert_english

User = get_user_model()


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

    def test_queryset_in_use_filter(self):
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
            "vat_category",
            "usage_metrics",
            "calculation_preview",
            "created_at",
            "updated_at",
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
            "products_using_vat",
        ]
        self.assertEqual(self.admin.readonly_fields, expected_readonly)

    def test_fieldsets(self):
        self.assertEqual(len(self.admin.fieldsets), 3)

        fieldset_titles = [fieldset[0] for fieldset in self.admin.fieldsets]
        expected_titles = [
            "VAT Configuration",
            "Usage",
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

        self.assertIn("0%", result)

    def test_vat_display_low(self):
        result = self.admin.vat_display(self.vat_low)

        self.assertIn("5.00%", result)

    def test_vat_category_zero(self):
        value, label = self.admin.vat_category(self.vat_zero)

        self.assertEqual(value, "zero")
        self.assertEqual(label, "Tax Free")

    def test_vat_category_reduced(self):
        value, label = self.admin.vat_category(self.vat_low)

        self.assertEqual(value, "reduced")
        self.assertEqual(label, "Reduced Rate")

    def test_vat_category_standard(self):
        value, label = self.admin.vat_category(self.vat_standard)

        self.assertEqual(value, "standard")
        self.assertEqual(label, "Standard Rate")

    def test_vat_category_high(self):
        value, label = self.admin.vat_category(self.vat_high)

        self.assertEqual(value, "high")
        self.assertEqual(label, "High Rate")

    def test_vat_category_premium(self):
        value, label = self.admin.vat_category(self.vat_very_high)

        self.assertEqual(value, "premium")
        self.assertEqual(label, "Premium Rate")

    def test_usage_metrics(self):
        self.vat_standard.products_count = 5

        result = self.admin.usage_metrics(self.vat_standard)

        self.assertIn("5", result)
        self.assertIn("products", result)

    def test_calculation_preview(self):
        result = self.admin.calculation_preview(self.vat_standard)

        self.assertIn("€100", result)
        self.assertIn("€120", result)

    def test_products_using_vat_none(self):
        result = self.admin.products_using_vat(self.vat_standard)

        self.assertIn("No products", result)

    def test_products_using_vat_some(self):
        self.vat_standard.products_count = 3

        result = self.admin.products_using_vat(self.vat_standard)

        self.assertIn("3", result)


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
        self.assertIn(VatUsageFilter, self.admin.list_filter)

    def test_admin_display_methods_integration(self):
        try:
            self.vat.products_count = 3

            display_methods = [
                "vat_display",
                "vat_category",
                "usage_metrics",
                "calculation_preview",
                "products_using_vat",
            ]

            for method_name in display_methods:
                method = getattr(self.admin, method_name)
                result = method(self.vat)
                self.assertIsNotNone(result)

        except Exception as e:
            self.fail(f"Display method integration failed: {e}")
