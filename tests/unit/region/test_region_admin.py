from datetime import timedelta
from unittest.mock import patch
from django.test import override_settings
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.utils import timezone

from country.models import Country
from region.admin import (
    CountryGroupFilter,
    RegionAdmin,
    RegionInline,
    RegionStatusFilter,
)
from region.models import Region

User = get_user_model()


class TestRegionStatusFilter(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.user = User.objects.create_user(
            email="test@example.com", password="testpass123"
        )

        class MockModelAdmin:
            pass

        self.model_admin = MockModelAdmin()

        self.country = Country.objects.create(
            alpha_2="US", alpha_3="USA", iso_cc="840"
        )

        self.region_with_name = Region.objects.create(
            alpha="CA", country=self.country
        )
        self.region_with_name.set_current_language("en")
        self.region_with_name.name = "California"
        self.region_with_name.save()

        self.region_without_name = Region.objects.create(
            alpha="TX", country=self.country
        )

        self.recent_region = Region.objects.create(
            alpha="NY", country=self.country
        )
        self.recent_region.created_at = timezone.now() - timedelta(days=15)
        self.recent_region.save()

        self.old_region = Region.objects.create(
            alpha="FL", country=self.country
        )
        self.old_region.created_at = timezone.now() - timedelta(days=45)
        self.old_region.save()

    def test_filter_title_and_parameter(self):
        filter_instance = RegionStatusFilter(
            self.request, {}, Region, self.model_admin
        )
        self.assertEqual(filter_instance.title, "Region Status")
        self.assertEqual(filter_instance.parameter_name, "region_status")

    def test_lookups(self):
        filter_instance = RegionStatusFilter(
            self.request, {}, Region, self.model_admin
        )

        lookups = filter_instance.lookups(self.request, None)
        expected_lookups = [
            ("has_name", "Has Name"),
            ("no_name", "No Name"),
            ("recent", "Recently Added"),
            ("by_continent", "Group by Continent"),
        ]

        self.assertEqual(list(lookups), expected_lookups)

    def test_queryset_has_name_filter(self):
        filter_instance = RegionStatusFilter(
            self.request,
            {},
            Region,
            self.model_admin,
        )
        filter_instance.used_parameters = {"region_status": "has_name"}

        queryset = Region.objects.all()
        filtered_queryset = filter_instance.queryset(self.request, queryset)

        regions_with_names = list(filtered_queryset)
        self.assertIn(self.region_with_name, regions_with_names)
        self.assertNotIn(self.region_without_name, regions_with_names)

    def test_queryset_no_name_filter(self):
        filter_instance = RegionStatusFilter(
            self.request, {}, Region, self.model_admin
        )
        filter_instance.used_parameters = {"region_status": "no_name"}

        queryset = Region.objects.all()
        filtered_queryset = filter_instance.queryset(self.request, queryset)

        regions_without_names = list(filtered_queryset)
        self.assertNotIn(self.region_with_name, regions_without_names)
        self.assertIn(self.region_without_name, regions_without_names)

    def test_queryset_recent_filter(self):
        filter_instance = RegionStatusFilter(
            self.request, {}, Region, self.model_admin
        )
        filter_instance.used_parameters = {"region_status": "recent"}

        queryset = Region.objects.all()
        filtered_queryset = filter_instance.queryset(self.request, queryset)

        recent_regions = list(filtered_queryset)
        self.assertIn(self.recent_region, recent_regions)
        self.assertNotIn(self.old_region, recent_regions)

    def test_queryset_no_filter(self):
        filter_instance = RegionStatusFilter(
            self.request, {}, Region, self.model_admin
        )

        queryset = Region.objects.all()
        filtered_queryset = filter_instance.queryset(self.request, queryset)

        self.assertEqual(list(queryset), list(filtered_queryset))


class TestCountryGroupFilter(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.user = User.objects.create_user(
            email="test@example.com", password="testpass123"
        )

        class MockModelAdmin:
            pass

        self.model_admin = MockModelAdmin()

        self.us_country = Country.objects.create(
            alpha_2="US", alpha_3="USA", iso_cc="840"
        )

        self.de_country = Country.objects.create(
            alpha_2="DE", alpha_3="DEU", iso_cc="276"
        )

        self.cn_country = Country.objects.create(
            alpha_2="CN", alpha_3="CHN", iso_cc="156"
        )

        self.us_region = Region.objects.create(
            alpha="CA", country=self.us_country
        )

        self.de_region = Region.objects.create(
            alpha="BY", country=self.de_country
        )

        self.cn_region = Region.objects.create(
            alpha="BJ", country=self.cn_country
        )

    def test_filter_title_and_parameter(self):
        filter_instance = CountryGroupFilter(
            self.request, {}, Region, self.model_admin
        )
        self.assertEqual(filter_instance.title, "Country Groups")
        self.assertEqual(filter_instance.parameter_name, "country_group")

    def test_lookups(self):
        filter_instance = CountryGroupFilter(
            self.request, {}, Region, self.model_admin
        )

        lookups = filter_instance.lookups(self.request, None)
        expected_lookups = [
            ("europe", "European Countries"),
            ("asia", "Asian Countries"),
            ("america", "American Countries"),
            ("africa", "African Countries"),
            ("oceania", "Oceania Countries"),
        ]

        self.assertEqual(list(lookups), expected_lookups)

    def test_queryset_europe_filter(self):
        filter_instance = CountryGroupFilter(
            self.request, {}, Region, self.model_admin
        )
        filter_instance.used_parameters = {"country_group": "europe"}

        queryset = Region.objects.all()
        filtered_queryset = filter_instance.queryset(self.request, queryset)

        european_regions = list(filtered_queryset)
        self.assertIn(self.de_region, european_regions)
        self.assertNotIn(self.us_region, european_regions)
        self.assertNotIn(self.cn_region, european_regions)

    def test_queryset_asia_filter(self):
        filter_instance = CountryGroupFilter(
            self.request, {}, Region, self.model_admin
        )
        filter_instance.used_parameters = {"country_group": "asia"}

        queryset = Region.objects.all()
        filtered_queryset = filter_instance.queryset(self.request, queryset)

        asian_regions = list(filtered_queryset)
        self.assertIn(self.cn_region, asian_regions)
        self.assertNotIn(self.us_region, asian_regions)
        self.assertNotIn(self.de_region, asian_regions)

    def test_queryset_america_filter(self):
        filter_instance = CountryGroupFilter(
            self.request, {}, Region, self.model_admin
        )
        filter_instance.used_parameters = {"country_group": "america"}

        queryset = Region.objects.all()
        filtered_queryset = filter_instance.queryset(self.request, queryset)

        american_regions = list(filtered_queryset)
        self.assertIn(self.us_region, american_regions)
        self.assertNotIn(self.de_region, american_regions)
        self.assertNotIn(self.cn_region, american_regions)

    def test_queryset_no_filter(self):
        filter_instance = CountryGroupFilter(
            self.request, {}, Region, self.model_admin
        )

        queryset = Region.objects.all()
        filtered_queryset = filter_instance.queryset(self.request, queryset)

        self.assertEqual(list(queryset), list(filtered_queryset))


class TestRegionInline(TestCase):
    def test_inline_configuration(self):
        inline = RegionInline(Region, AdminSite())

        self.assertEqual(inline.model, Region)
        self.assertEqual(inline.extra, 0)
        self.assertEqual(inline.fields, ("alpha", "name", "sort_order"))
        self.assertEqual(inline.readonly_fields, ("sort_order",))
        self.assertTrue(inline.tab)
        self.assertTrue(inline.show_change_link)

    def test_inline_inheritance(self):
        from parler.admin import TranslatableTabularInline

        self.assertTrue(issubclass(RegionInline, TranslatableTabularInline))


@override_settings(LANGUAGE_CODE="en-US")
class TestRegionAdmin(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.user = User.objects.create_user(
            email="test@example.com", password="testpass123"
        )

        self.site = AdminSite()
        self.admin = RegionAdmin(Region, self.site)

        self.country = Country.objects.create(
            alpha_2="US", alpha_3="USA", iso_cc="840"
        )
        self.country.set_current_language("en")
        self.country.name = "United States"
        self.country.save()

        self.region = Region.objects.create(
            alpha="CA", country=self.country, sort_order=1
        )
        self.region.set_current_language("en")
        self.region.name = "California"
        self.region.save()

    def test_admin_configuration(self):
        self.assertTrue(self.admin.compressed_fields)
        self.assertTrue(self.admin.warn_unsaved_form)
        self.assertTrue(self.admin.list_fullwidth)
        self.assertTrue(self.admin.list_filter_submit)
        self.assertTrue(self.admin.list_filter_sheet)
        self.assertEqual(self.admin.list_per_page, 50)

    def test_list_display(self):
        expected_display = [
            "region_info",
            "country_display",
            "region_stats",
            "sort_display",
            "completeness_badge",
            "created_display",
        ]
        self.assertEqual(self.admin.list_display, expected_display)

    def test_list_filter(self):
        from unfold.contrib.filters.admin import (
            RangeDateTimeFilter,
            RelatedDropdownFilter,
        )

        expected_filters = [
            RegionStatusFilter,
            CountryGroupFilter,
            ("country", RelatedDropdownFilter),
            ("created_at", RangeDateTimeFilter),
            ("updated_at", RangeDateTimeFilter),
        ]
        self.assertEqual(self.admin.list_filter, expected_filters)

    def test_search_fields(self):
        expected_fields = [
            "alpha",
            "translations__name",
            "country__alpha_2",
            "country__alpha_3",
            "country__translations__name",
        ]
        self.assertEqual(self.admin.search_fields, expected_fields)

    def test_readonly_fields(self):
        expected_fields = (
            "uuid",
            "sort_order",
            "created_at",
            "updated_at",
            "region_analytics",
            "country_analytics",
        )
        self.assertEqual(self.admin.readonly_fields, expected_fields)

    def test_get_queryset(self):
        queryset = self.admin.get_queryset(self.request)
        self.assertIsNotNone(queryset)

    def test_region_info_with_name(self):
        result = self.admin.region_info(self.region)

        self.assertIn("California", result)
        self.assertIn("CA", result)
        self.assertIn("text-green-600", result)
        self.assertIn("✅", result)

    def test_region_info_without_name(self):
        region_no_name = Region.objects.create(alpha="TX", country=self.country)

        result = self.admin.region_info(region_no_name)

        self.assertIn("Unnamed Region", result)
        self.assertIn("TX", result)
        self.assertIn("text-orange-600", result)
        self.assertIn("⚠️", result)

    def test_country_display_with_flag(self):
        with patch.object(self.country, "image_flag") as mock_flag:
            mock_flag.url = "/media/flags/us.png"
            mock_flag.__bool__ = lambda self: True

            result = self.admin.country_display(self.region)

            self.assertIn("United States", result)
            self.assertIn("US", result)
            self.assertIn('<img src="/media/flags/us.png"', result)

    def test_country_display_without_flag(self):
        result = self.admin.country_display(self.region)

        self.assertIn("United States", result)
        self.assertIn("US", result)
        self.assertIn("🏳️", result)

    def test_region_stats_numeric_code(self):
        numeric_region = Region.objects.create(
            alpha="123", country=self.country
        )

        result = self.admin.region_stats(numeric_region)

        self.assertIn("3 chars", result)
        self.assertIn("🔢 Numeric", result)

    def test_region_stats_alpha_code(self):
        result = self.admin.region_stats(self.region)

        self.assertIn("2 chars", result)
        self.assertIn("🔤 Alpha", result)

    def test_region_stats_mixed_code(self):
        mixed_region = Region.objects.create(alpha="1A", country=self.country)

        result = self.admin.region_stats(mixed_region)

        self.assertIn("2 chars", result)
        self.assertIn("🔀 Mixed", result)

    def test_sort_display_with_order(self):
        result = self.admin.sort_display(self.region)

        self.assertIn("1", result)
        self.assertIn("#1", result)
        self.assertIn("bg-green-50", result)
        self.assertIn("✅", result)

    def test_sort_display_no_order(self):
        no_order_region = Region.objects.create(
            alpha="TX", country=self.country, sort_order=None
        )

        result = self.admin.sort_display(no_order_region)

        self.assertIn("None", result)
        self.assertIn("No Order", result)
        self.assertIn("bg-red-50", result)
        self.assertIn("❌", result)

    def test_sort_display_first_order(self):
        first_region = Region.objects.create(
            alpha="TX", country=self.country, sort_order=0
        )

        result = self.admin.sort_display(first_region)

        self.assertIn("0", result)
        self.assertIn("First", result)
        self.assertIn("bg-yellow-50", result)
        self.assertIn("⚠️", result)

    def test_completeness_badge_complete(self):
        result = self.admin.completeness_badge(self.region)

        self.assertIn("100%", result)
        self.assertIn("Complete", result)
        self.assertIn("bg-green-50", result)
        self.assertIn("✅", result)

    def test_completeness_badge_partial(self):
        partial_region = Region.objects.create(
            alpha="TX",
            country=self.country,
        )

        result = self.admin.completeness_badge(partial_region)

        self.assertIn("66%", result)
        self.assertIn("Good", result)
        self.assertIn("bg-blue-50", result)
        self.assertIn("🔷", result)

    def test_completeness_badge_poor(self):
        poor_region = Region.objects.create(
            alpha="",
            country=self.country,
        )

        result = self.admin.completeness_badge(poor_region)

        self.assertIn("33%", result)
        self.assertIn("Partial", result)
        self.assertIn("bg-yellow-50", result)
        self.assertIn("⚠️", result)

    def test_created_display(self):
        result = self.admin.created_display(self.region)

        date_str = self.region.created_at.strftime("%Y-%m-%d")
        time_str = self.region.created_at.strftime("%H:%M")
        self.assertIn(date_str, result)
        self.assertIn(time_str, result)

    def test_region_analytics(self):
        result = self.admin.region_analytics(self.region)

        self.assertIn("Name Length", result)
        self.assertIn("Code Length", result)
        self.assertIn("Code Pattern", result)
        self.assertIn("Case Pattern", result)
        self.assertIn("Has Name", result)
        self.assertIn("Sort Position", result)

        self.assertIn("10 chars", result)
        self.assertIn("2 chars", result)
        self.assertIn("Alpha", result)
        self.assertIn("Upper", result)
        self.assertIn("Yes", result)
        self.assertIn("1", result)

    def test_country_analytics(self):
        result = self.admin.country_analytics(self.region)

        self.assertIn("Country", result)
        self.assertIn("Country Code", result)
        self.assertIn("Total Regions", result)
        self.assertIn("Position", result)
        self.assertIn("Has Flag", result)
        self.assertIn("Has ISO", result)

        self.assertIn("United States", result)
        self.assertIn("US", result)

    def test_update_sort_order_action(self):
        Region.objects.create(alpha="TX", country=self.country)
        Region.objects.create(alpha="FL", country=self.country)

        queryset = Region.objects.filter(country=self.country)

        setattr(self.request, "session", {})
        messages = FallbackStorage(self.request)
        setattr(self.request, "_messages", messages)

        self.admin.update_sort_order(self.request, queryset)

        updated_regions = Region.objects.filter(country=self.country).order_by(
            "sort_order"
        )
        regions_list = list(updated_regions)

        self.assertEqual(regions_list[0].alpha, "CA")
        self.assertEqual(regions_list[1].alpha, "FL")
        self.assertEqual(regions_list[2].alpha, "TX")

        self.assertEqual(regions_list[0].sort_order, 0)
        self.assertEqual(regions_list[1].sort_order, 1)
        self.assertEqual(regions_list[2].sort_order, 2)

    def test_method_short_descriptions(self):
        from django.utils.translation import gettext_lazy as _

        self.assertEqual(self.admin.region_info.short_description, _("Region"))
        self.assertEqual(
            self.admin.country_display.short_description, _("Country")
        )
        self.assertEqual(self.admin.region_stats.short_description, _("Stats"))
        self.assertEqual(
            self.admin.sort_display.short_description, _("Sort Order")
        )
        self.assertEqual(
            self.admin.completeness_badge.short_description, _("Completeness")
        )
        self.assertEqual(
            self.admin.created_display.short_description, _("Created")
        )
        self.assertEqual(
            self.admin.region_analytics.short_description, _("Region Analytics")
        )
        self.assertEqual(
            self.admin.country_analytics.short_description,
            _("Country Analytics"),
        )


class TestRegionAdminIntegration(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.user = User.objects.create_user(
            email="test@example.com", password="testpass123"
        )

        self.site = AdminSite()
        self.admin = RegionAdmin(Region, self.site)

    def test_admin_inheritance(self):
        from parler.admin import TranslatableAdmin
        from unfold.admin import ModelAdmin

        self.assertIsInstance(self.admin, ModelAdmin)
        self.assertIsInstance(self.admin, TranslatableAdmin)

    def test_admin_has_required_methods(self):
        required_methods = [
            "get_queryset",
            "region_info",
            "country_display",
            "region_stats",
            "sort_display",
            "completeness_badge",
            "created_display",
            "region_analytics",
            "country_analytics",
            "update_sort_order",
        ]

        for method_name in required_methods:
            self.assertTrue(hasattr(self.admin, method_name))
            self.assertTrue(callable(getattr(self.admin, method_name)))

    def test_fieldsets_structure(self):
        fieldsets = self.admin.fieldsets
        self.assertIsInstance(fieldsets, tuple)
        self.assertGreater(len(fieldsets), 1)

    def test_actions_configuration(self):
        self.assertIn("update_sort_order", self.admin.actions)


class TestRegionAdminEdgeCases(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.user = User.objects.create_user(
            email="test@example.com", password="testpass123"
        )

        self.site = AdminSite()
        self.admin = RegionAdmin(Region, self.site)

    def test_region_info_empty_alpha(self):
        country = Country.objects.create(
            alpha_2="US", alpha_3="USA", iso_cc="840"
        )

        empty_region = Region.objects.create(alpha="", country=country)

        result = self.admin.region_info(empty_region)

        self.assertIn("Unnamed Region", result)

    def test_analytics_with_special_characters(self):
        country = Country.objects.create(
            alpha_2="US", alpha_3="USA", iso_cc="840"
        )

        special_region = Region.objects.create(alpha="CA-1", country=country)

        result = self.admin.region_analytics(special_region)

        self.assertIn("Code Length", result)
        self.assertIn("Mixed", result)

    def test_completeness_calculation_edge_cases(self):
        country = Country.objects.create(
            alpha_2="US", alpha_3="USA", iso_cc="840"
        )
        minimal_region = Region.objects.create(alpha="XX", country=country)

        result = self.admin.completeness_badge(minimal_region)

        self.assertIn("%", result)

    def test_sort_order_action_empty_queryset(self):
        empty_queryset = Region.objects.none()

        setattr(self.request, "session", {})
        messages = FallbackStorage(self.request)
        setattr(self.request, "_messages", messages)

        try:
            self.admin.update_sort_order(self.request, empty_queryset)
        except Exception as e:
            self.fail(
                f"update_sort_order should handle empty queryset gracefully: {e}"
            )

    def test_display_methods_with_none_values(self):
        country = Country.objects.create(
            alpha_2="US", alpha_3="USA", iso_cc="840"
        )

        region_with_nones = Region.objects.create(
            alpha="XX", country=country, sort_order=None
        )

        try:
            self.admin.region_info(region_with_nones)
            self.admin.country_display(region_with_nones)
            self.admin.region_stats(region_with_nones)
            self.admin.sort_display(region_with_nones)
            self.admin.completeness_badge(region_with_nones)
            self.admin.created_display(region_with_nones)
            self.admin.region_analytics(region_with_nones)
            self.admin.country_analytics(region_with_nones)
        except Exception as e:
            self.fail(
                f"Display methods should handle None values gracefully: {e}"
            )
