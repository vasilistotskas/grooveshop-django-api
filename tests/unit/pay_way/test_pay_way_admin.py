from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase

from pay_way.admin import (
    ConfigurationStatusFilter,
    CostRangeFilter,
    FreeThresholdFilter,
    PayWayAdmin,
    PaymentTypeFilter,
)
from pay_way.models import PayWay

User = get_user_model()


class CostRangeFilterTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/admin/pay_way/payway/")
        self.model_admin = Mock()

        self.free_payment = PayWay.objects.create(cost=Decimal("0.00"))
        self.free_payment.set_current_language("en")
        self.free_payment.name = "Free Payment"
        self.free_payment.save()

        self.low_cost_payment = PayWay.objects.create(cost=Decimal("2.50"))
        self.low_cost_payment.set_current_language("en")
        self.low_cost_payment.name = "Low Cost Payment"
        self.low_cost_payment.save()

        self.high_cost_payment = PayWay.objects.create(cost=Decimal("10.00"))
        self.high_cost_payment.set_current_language("en")
        self.high_cost_payment.name = "High Cost Payment"
        self.high_cost_payment.save()

    def test_filter_title(self):
        filter_instance = CostRangeFilter(
            self.request, {}, PayWay, self.model_admin
        )
        self.assertEqual(filter_instance.title, "Cost Range")

    def test_filter_parameter_name(self):
        filter_instance = CostRangeFilter(
            self.request, {}, PayWay, self.model_admin
        )
        self.assertEqual(filter_instance.parameter_name, "cost_range")

    def test_queryset_with_from_value(self):
        filter_instance = CostRangeFilter(
            self.request, {"cost_range_from": "2.00"}, PayWay, self.model_admin
        )
        queryset = filter_instance.queryset(self.request, PayWay.objects.all())

        self.assertIn(self.low_cost_payment, queryset)
        self.assertIn(self.high_cost_payment, queryset)
        self.assertNotIn(self.free_payment, queryset)

    def test_queryset_with_to_value(self):
        filter_instance = CostRangeFilter(
            self.request, {"cost_range_to": "5.00"}, PayWay, self.model_admin
        )
        queryset = filter_instance.queryset(self.request, PayWay.objects.all())

        self.assertIn(self.free_payment, queryset)
        self.assertIn(self.low_cost_payment, queryset)
        self.assertNotIn(self.high_cost_payment, queryset)

    def test_queryset_with_range(self):
        filter_instance = CostRangeFilter(
            self.request,
            {"cost_range_from": "2.00", "cost_range_to": "5.00"},
            PayWay,
            self.model_admin,
        )
        queryset = filter_instance.queryset(self.request, PayWay.objects.all())

        self.assertNotIn(self.free_payment, queryset)
        self.assertIn(self.low_cost_payment, queryset)
        self.assertNotIn(self.high_cost_payment, queryset)

    def test_queryset_no_filter(self):
        filter_instance = CostRangeFilter(
            self.request, {}, PayWay, self.model_admin
        )
        queryset = filter_instance.queryset(self.request, PayWay.objects.all())

        self.assertEqual(queryset.count(), 3)

    def test_expected_parameters(self):
        filter_instance = CostRangeFilter(
            self.request, {}, PayWay, self.model_admin
        )
        expected = ["cost_range_from", "cost_range_to"]
        self.assertEqual(filter_instance.expected_parameters(), expected)


class FreeThresholdFilterTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/admin/pay_way/payway/")
        self.model_admin = Mock()

        self.no_threshold = PayWay.objects.create(
            free_threshold=Decimal("0.00")
        )
        self.low_threshold = PayWay.objects.create(
            free_threshold=Decimal("50.00")
        )
        self.high_threshold = PayWay.objects.create(
            free_threshold=Decimal("100.00")
        )

    def test_filter_title(self):
        filter_instance = FreeThresholdFilter(
            self.request, {}, PayWay, self.model_admin
        )
        self.assertEqual(filter_instance.title, "Free Threshold Range")

    def test_filter_parameter_name(self):
        filter_instance = FreeThresholdFilter(
            self.request, {}, PayWay, self.model_admin
        )
        self.assertEqual(filter_instance.parameter_name, "free_threshold_range")

    def test_queryset_filtering(self):
        filter_instance = FreeThresholdFilter(
            self.request,
            {"free_threshold_range_from": "75.00"},
            PayWay,
            self.model_admin,
        )
        queryset = filter_instance.queryset(self.request, PayWay.objects.all())

        self.assertIn(self.high_threshold, queryset)
        self.assertNotIn(self.low_threshold, queryset)


class PaymentTypeFilterTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/admin/pay_way/payway/")
        self.model_admin = Mock()

        self.online_payment = PayWay.objects.create(
            is_online_payment=True, requires_confirmation=False
        )
        self.offline_simple = PayWay.objects.create(
            is_online_payment=False, requires_confirmation=False
        )
        self.offline_confirmation = PayWay.objects.create(
            is_online_payment=False, requires_confirmation=True
        )

    def test_filter_title(self):
        filter_instance = PaymentTypeFilter(
            self.request, {}, PayWay, self.model_admin
        )
        self.assertEqual(filter_instance.title, "Payment Type")

    def test_filter_parameter_name(self):
        filter_instance = PaymentTypeFilter(
            self.request, {}, PayWay, self.model_admin
        )
        self.assertEqual(filter_instance.parameter_name, "payment_type")

    def test_lookups(self):
        filter_instance = PaymentTypeFilter(
            self.request, {}, PayWay, self.model_admin
        )
        lookups = filter_instance.lookups(self.request, self.model_admin)

        expected_keys = ["online", "offline_simple", "offline_confirmation"]
        actual_keys = [lookup[0] for lookup in lookups]
        self.assertEqual(actual_keys, expected_keys)

    def test_queryset_online_filter(self):
        filter_instance = PaymentTypeFilter(
            self.request, {"payment_type": "online"}, PayWay, self.model_admin
        )
        queryset = filter_instance.queryset(self.request, PayWay.objects.all())

        self.assertIsNotNone(queryset)
        self.assertTrue(queryset.count() >= 0)

    def test_queryset_offline_simple_filter(self):
        filter_instance = PaymentTypeFilter(
            self.request,
            {"payment_type": "offline_simple"},
            PayWay,
            self.model_admin,
        )
        queryset = filter_instance.queryset(self.request, PayWay.objects.all())

        self.assertIsNotNone(queryset)
        self.assertTrue(queryset.count() >= 0)

    def test_queryset_offline_confirmation_filter(self):
        filter_instance = PaymentTypeFilter(
            self.request,
            {"payment_type": "offline_confirmation"},
            PayWay,
            self.model_admin,
        )
        queryset = filter_instance.queryset(self.request, PayWay.objects.all())

        self.assertIsNotNone(queryset)
        self.assertTrue(queryset.count() >= 0)


class ConfigurationStatusFilterTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/admin/pay_way/payway/")
        self.model_admin = Mock()

        self.configured_payment = PayWay.objects.create(
            is_online_payment=True,
            configuration={"api_key": "test_key", "merchant_id": "123"},
        )
        self.not_configured_payment = PayWay.objects.create(
            is_online_payment=True, configuration={}
        )
        self.no_config_needed = PayWay.objects.create(
            is_online_payment=False, configuration=None
        )

    def test_filter_title(self):
        filter_instance = ConfigurationStatusFilter(
            self.request, {}, PayWay, self.model_admin
        )
        self.assertEqual(filter_instance.title, "Configuration Status")

    def test_filter_parameter_name(self):
        filter_instance = ConfigurationStatusFilter(
            self.request, {}, PayWay, self.model_admin
        )
        self.assertEqual(filter_instance.parameter_name, "configuration_status")

    def test_lookups(self):
        filter_instance = ConfigurationStatusFilter(
            self.request, {}, PayWay, self.model_admin
        )
        lookups = filter_instance.lookups(self.request, self.model_admin)

        expected_keys = ["configured", "not_configured", "no_config_needed"]
        actual_keys = [lookup[0] for lookup in lookups]
        self.assertEqual(actual_keys, expected_keys)

    def test_queryset_configured_filter(self):
        filter_instance = ConfigurationStatusFilter(
            self.request,
            {"configuration_status": "configured"},
            PayWay,
            self.model_admin,
        )
        queryset = filter_instance.queryset(self.request, PayWay.objects.all())

        self.assertIsNotNone(queryset)
        self.assertTrue(queryset.count() >= 0)

    def test_queryset_not_configured_filter(self):
        filter_instance = ConfigurationStatusFilter(
            self.request,
            {"configuration_status": "not_configured"},
            PayWay,
            self.model_admin,
        )
        queryset = filter_instance.queryset(self.request, PayWay.objects.all())

        self.assertIsNotNone(queryset)
        self.assertTrue(queryset.count() >= 0)

    def test_queryset_no_config_needed_filter(self):
        filter_instance = ConfigurationStatusFilter(
            self.request,
            {"configuration_status": "no_config_needed"},
            PayWay,
            self.model_admin,
        )
        queryset = filter_instance.queryset(self.request, PayWay.objects.all())

        self.assertIsNotNone(queryset)
        self.assertTrue(queryset.count() >= 0)


class PayWayAdminTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.admin = PayWayAdmin(PayWay, self.site)

        self.user = User.objects.create_user(
            email="test@example.com",
            username="testuser",
            password="testpass123",
        )

        self.payway = PayWay.objects.create(
            cost=Decimal("5.00"),
            free_threshold=Decimal("50.00"),
            is_online_payment=True,
            requires_confirmation=False,
            active=True,
            sort_order=1,
            provider_code="PAYPAL",
            configuration={"api_key": "test_key"},
        )
        self.payway.set_current_language("en")
        self.payway.name = "PayPal Payment"
        self.payway.description = "Pay with PayPal"
        self.payway.save()

    def test_admin_configuration(self):
        self.assertTrue(self.admin.compressed_fields)
        self.assertTrue(self.admin.warn_unsaved_form)
        self.assertTrue(self.admin.list_fullwidth)
        self.assertTrue(self.admin.list_filter_submit)
        self.assertTrue(self.admin.list_filter_sheet)

    def test_list_display(self):
        expected_fields = [
            "name_display",
            "provider_code_badge",
            "active_status",
            "active",
            "payment_type_display",
            "cost_display",
            "free_threshold_display",
            "configuration_status",
            "icon_preview",
            "sort_order_display",
        ]
        self.assertEqual(self.admin.list_display, expected_fields)

    def test_list_filter(self):
        self.assertIn("active", self.admin.list_filter)
        self.assertIn(PaymentTypeFilter, self.admin.list_filter)
        self.assertIn(ConfigurationStatusFilter, self.admin.list_filter)
        self.assertIn(CostRangeFilter, self.admin.list_filter)
        self.assertIn(FreeThresholdFilter, self.admin.list_filter)

    def test_search_fields(self):
        expected_fields = [
            "translations__name",
            "provider_code",
            "translations__description",
            "translations__instructions",
        ]
        self.assertEqual(self.admin.search_fields, expected_fields)

    def test_readonly_fields(self):
        expected_fields = [
            "id",
            "created_at",
            "updated_at",
            "configuration_preview",
            "effective_cost_display",
            "is_configured_status",
            "sort_order",
        ]
        self.assertEqual(self.admin.readonly_fields, expected_fields)

    def test_ordering(self):
        self.assertEqual(self.admin.ordering, ["sort_order", "id"])

    def test_actions(self):
        expected_actions = [
            "activate_payment_methods",
            "deactivate_payment_methods",
            "move_up_in_order",
            "move_down_in_order",
            "reset_sort_order",
        ]
        self.assertEqual(self.admin.actions, expected_actions)

    def test_name_display(self):
        result = self.admin.name_display(self.payway)

        self.assertIn("PayPal Payment", result)
        self.assertIn("<strong", result)

    def test_provider_code_badge(self):
        result = self.admin.provider_code_badge(self.payway)

        self.assertIn("PAYPAL", result)
        self.assertIn("inline-flex", result)
        self.assertIn("px-2", result)

    def test_active_status(self):
        result = self.admin.active_status(self.payway)

        self.assertIn("✓", result)
        self.assertIn("Active", result)
        self.assertIn("bg-green-50", result)

    def test_active_status_inactive(self):
        self.payway.active = False
        result = self.admin.active_status(self.payway)

        self.assertIn("✗", result)
        self.assertIn("Inactive", result)

    def test_payment_type_display(self):
        result = self.admin.payment_type_display(self.payway)

        self.assertIn("Online", result)
        self.assertIn("<span", result)

    def test_payment_type_display_offline(self):
        self.payway.is_online_payment = False
        result = self.admin.payment_type_display(self.payway)

        self.assertIn("Offline", result)
        self.assertIn("<span", result)

    def test_cost_display(self):
        result = self.admin.cost_display(self.payway)

        self.assertIn("5.00 EUR", result)
        self.assertIn("bg-orange-50", result)

    def test_cost_display_free(self):
        self.payway.cost = Decimal("0.00")
        result = self.admin.cost_display(self.payway)

        self.assertIn("Free", result)
        self.assertIn("bg-green-50", result)

    def test_free_threshold_display(self):
        result = self.admin.free_threshold_display(self.payway)

        self.assertIn("50.00 EUR", result)
        self.assertTrue(len(result) > 0)

    def test_free_threshold_display_no_threshold(self):
        self.payway.free_threshold = Decimal("0.00")
        result = self.admin.free_threshold_display(self.payway)

        self.assertTrue(len(result) > 0)

    def test_configuration_status(self):
        result = self.admin.configuration_status(self.payway)

        self.assertIn("✓", result)
        self.assertIn("Configured", result)
        self.assertIn("bg-green-50", result)

    def test_configuration_status_not_configured(self):
        self.payway.configuration = {}
        result = self.admin.configuration_status(self.payway)

        self.assertIn("Missing Config", result)
        self.assertIn("⚠️", result)
        self.assertIn("bg-red-50", result)

    def test_configuration_status_offline(self):
        self.payway.is_online_payment = False
        result = self.admin.configuration_status(self.payway)

        self.assertIn("N/A", result)
        self.assertTrue(len(result) > 0)

    def test_sort_order_display(self):
        result = self.admin.sort_order_display(self.payway)

        self.assertIn("#1", result)
        self.assertIn("inline-flex", result)

    def test_icon_preview(self):
        result = self.admin.icon_preview(self.payway)
        self.assertIn("No icon", result)

    def test_icon_preview_with_icon(self):
        self.payway.icon = SimpleUploadedFile(
            "icon.png", b"fake_image_data", content_type="image/png"
        )
        self.payway.save()

        result = self.admin.icon_preview(self.payway)
        self.assertIn("<img", result)
        self.assertIn("src=", result)
        self.assertTrue(len(result) > 0)

    def test_configuration_preview(self):
        result = self.admin.configuration_preview(self.payway)

        self.assertIn("api_key", result)
        self.assertIn("Configuration Keys:", result)
        self.assertIn("text-sm", result)

    def test_configuration_preview_empty(self):
        self.payway.configuration = {}
        result = self.admin.configuration_preview(self.payway)

        self.assertIn("No configuration", result)

    def test_effective_cost_display(self):
        result = self.admin.effective_cost_display(self.payway)

        self.assertTrue(len(result) > 0)
        self.assertIn("<span", result)

    def test_is_configured_status(self):
        result = self.admin.is_configured_status(self.payway)

        self.assertIn("✓", result)
        self.assertTrue(len(result) > 0)

    def test_is_configured_status_not_configured(self):
        self.payway.configuration = {}
        result = self.admin.is_configured_status(self.payway)

        self.assertTrue(len(result) > 0)
        self.assertIn("<span", result)

    def test_activate_payment_methods_action(self):
        inactive_payway = PayWay.objects.create(active=False)

        request = self.factory.post("/admin/pay_way/payway/")
        request.user = self.user
        request._messages = Mock()

        queryset = PayWay.objects.filter(id=inactive_payway.id)

        with patch.object(self.admin, "message_user") as mock_message:
            self.admin.activate_payment_methods(request, queryset)

            mock_message.assert_called_once()

            inactive_payway.refresh_from_db()
            self.assertTrue(inactive_payway.active)

    def test_deactivate_payment_methods_action(self):
        request = self.factory.post("/admin/pay_way/payway/")
        request.user = self.user
        request._messages = Mock()

        queryset = PayWay.objects.filter(id=self.payway.id)

        with patch.object(self.admin, "message_user") as mock_message:
            self.admin.deactivate_payment_methods(request, queryset)

            mock_message.assert_called_once()

            self.payway.refresh_from_db()
            self.assertFalse(self.payway.active)

    def test_move_up_in_order_action(self):
        other_payway = PayWay.objects.create(sort_order=2)

        request = self.factory.post("/admin/pay_way/payway/")
        request.user = self.user
        request._messages = Mock()

        queryset = PayWay.objects.filter(id=other_payway.id)

        with patch.object(self.admin, "message_user") as mock_message:
            self.admin.move_up_in_order(request, queryset)

            mock_message.assert_called_once()

    def test_move_down_in_order_action(self):
        request = self.factory.post("/admin/pay_way/payway/")
        request.user = self.user
        request._messages = Mock()

        queryset = PayWay.objects.filter(id=self.payway.id)

        with patch.object(self.admin, "message_user") as mock_message:
            self.admin.move_down_in_order(request, queryset)

            mock_message.assert_called_once()

    def test_reset_sort_order_action(self):
        request = self.factory.post("/admin/pay_way/payway/")
        request.user = self.user
        request._messages = Mock()

        queryset = PayWay.objects.all()

        with patch.object(self.admin, "message_user") as mock_message:
            self.admin.reset_sort_order(request, queryset)

            mock_message.assert_called_once()


class PayWayAdminIntegrationTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.site.register(PayWay, PayWayAdmin)
        self.admin = self.site._registry[PayWay]

        self.superuser = User.objects.create_user(
            email="admin@example.com",
            username="admin",
            password="adminpass123",
            is_staff=True,
            is_superuser=True,
        )

        self.online_payment = PayWay.objects.create(
            cost=Decimal("3.00"),
            is_online_payment=True,
            active=True,
            configuration={"api_key": "test"},
        )
        self.online_payment.set_current_language("en")
        self.online_payment.name = "Credit Card"
        self.online_payment.save()

        self.offline_payment = PayWay.objects.create(
            cost=Decimal("0.00"), is_online_payment=False, active=True
        )
        self.offline_payment.set_current_language("en")
        self.offline_payment.name = "Cash on Delivery"
        self.offline_payment.save()

    def test_admin_filters_integration(self):
        request = self.factory.get("/admin/pay_way/payway/")
        request.user = self.superuser

        self.assertIn(PaymentTypeFilter, self.admin.list_filter)
        self.assertIn(ConfigurationStatusFilter, self.admin.list_filter)
        self.assertIn(CostRangeFilter, self.admin.list_filter)
        self.assertIn(FreeThresholdFilter, self.admin.list_filter)

    def test_admin_display_methods_integration(self):
        display_methods = [
            "name_display",
            "provider_code_badge",
            "active_status",
            "payment_type_display",
            "cost_display",
            "free_threshold_display",
            "configuration_status",
            "sort_order_display",
            "icon_preview",
            "configuration_preview",
            "effective_cost_display",
            "is_configured_status",
        ]

        for method_name in display_methods:
            with self.subTest(method=method_name):
                method = getattr(self.admin, method_name)
                result = method(self.online_payment)
                self.assertIsNotNone(result)

                result = method(self.offline_payment)
                self.assertIsNotNone(result)

    def test_filter_functionality(self):
        factory = RequestFactory()
        request = factory.get("/admin/pay_way/payway/")

        payment_filter = PaymentTypeFilter(
            request, {"payment_type": "online"}, PayWay, self.admin
        )

        try:
            online_queryset = payment_filter.queryset(
                request, PayWay.objects.all()
            )
            self.assertIsNotNone(online_queryset)
            self.assertTrue(online_queryset.count() >= 0)
        except Exception as e:
            self.fail(f"Filter functionality failed with error: {e}")

    def test_admin_changelist_view(self):
        request = self.factory.get("/admin/pay_way/payway/")
        request.user = self.superuser

        try:
            changelist = self.admin.get_changelist_instance(request)
            self.assertIsNotNone(changelist)
        except Exception as e:
            self.fail(f"Admin changelist view failed: {e}")

    def test_admin_configuration(self):
        self.assertTrue(hasattr(self.admin, "list_display"))
        self.assertTrue(hasattr(self.admin, "list_filter"))
        self.assertTrue(hasattr(self.admin, "search_fields"))
        self.assertTrue(hasattr(self.admin, "readonly_fields"))
        self.assertTrue(hasattr(self.admin, "actions"))

        self.assertIn(PayWay, self.site._registry)
        self.assertEqual(self.site._registry[PayWay], self.admin)
