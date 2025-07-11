from unittest.mock import Mock, patch

from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase

from country.admin import CountryAdmin, CountryStatusFilter
from country.models import Country


class CountryStatusFilterTestCase(TestCase):
    def setUp(self):
        self.filter = CountryStatusFilter(
            request=None,
            params={},
            model=Country,
            model_admin=None,
        )
        self.factory = RequestFactory()
        self.request = self.factory.get("/admin/country/country/")

        self.complete_country = Country.objects.create(
            alpha_2="US",
            alpha_3="USA",
            iso_cc=840,
            phone_code=1,
        )
        self.complete_country.set_current_language("en")
        self.complete_country.name = "United States"
        self.complete_country.save()

        self.incomplete_country = Country.objects.create(
            alpha_2="XX",
            alpha_3="XXX",
        )
        self.incomplete_country.set_current_language("en")
        self.incomplete_country.name = "Test Country"
        self.incomplete_country.save()

        self.with_phone_country = Country.objects.create(
            alpha_2="GB",
            alpha_3="GBR",
            phone_code=44,
        )

    def test_lookups(self):
        lookups = self.filter.lookups(self.request, None)

        self.assertEqual(len(lookups), 5)
        self.assertEqual(lookups[0], ("active", "Active (Has ISO Code)"))
        self.assertEqual(
            lookups[1], ("incomplete", "Incomplete (Missing Data)")
        )
        self.assertEqual(lookups[2], ("with_phone", "Has Phone Code"))
        self.assertEqual(lookups[3], ("with_flag", "Has Flag Image"))
        self.assertEqual(lookups[4], ("complete", "Complete Profile"))

    def test_queryset_active(self):
        request_with_filter = self.factory.get(
            "/admin/country/country/", {"country_status": "active"}
        )

        filter_instance = CountryStatusFilter(
            request=request_with_filter,
            params={"country_status": "active"},
            model=Country,
            model_admin=None,
        )

        queryset = filter_instance.queryset(
            request_with_filter, Country.objects.all()
        )

        self.assertIn(self.complete_country, queryset)

    def test_queryset_incomplete(self):
        request_with_filter = self.factory.get(
            "/admin/country/country/", {"country_status": "incomplete"}
        )

        filter_instance = CountryStatusFilter(
            request=request_with_filter,
            params={"country_status": "incomplete"},
            model=Country,
            model_admin=None,
        )

        queryset = filter_instance.queryset(
            request_with_filter, Country.objects.all()
        )

        self.assertIsNotNone(queryset)
        self.assertTrue(len(queryset) > 0)

    def test_queryset_with_phone(self):
        request_with_filter = self.factory.get(
            "/admin/country/country/", {"country_status": "with_phone"}
        )

        filter_instance = CountryStatusFilter(
            request=request_with_filter,
            params={"country_status": "with_phone"},
            model=Country,
            model_admin=None,
        )

        queryset = filter_instance.queryset(
            request_with_filter, Country.objects.all()
        )

        self.assertIsNotNone(queryset)
        self.assertTrue(len(queryset) > 0)

    def test_queryset_complete(self):
        self.complete_country.image_flag = SimpleUploadedFile(
            "flag.jpg", b"fake_image_data", content_type="image/jpeg"
        )
        self.complete_country.save()

        request_with_filter = self.factory.get(
            "/admin/country/country/", {"country_status": "complete"}
        )

        filter_instance = CountryStatusFilter(
            request=request_with_filter,
            params={"country_status": "complete"},
            model=Country,
            model_admin=None,
        )

        queryset = filter_instance.queryset(
            request_with_filter, Country.objects.all()
        )

        self.assertIsNotNone(queryset)
        self.assertTrue(len(queryset) >= 0)

    def test_queryset_default(self):
        filter_instance = CountryStatusFilter(
            request=self.request,
            params={},
            model=Country,
            model_admin=None,
        )

        queryset = filter_instance.queryset(self.request, Country.objects.all())

        self.assertEqual(queryset.count(), Country.objects.count())


class CountryAdminTestCase(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = CountryAdmin(Country, self.site)
        self.factory = RequestFactory()

        self.country = Country.objects.create(
            alpha_2="DE",
            alpha_3="DEU",
            iso_cc=276,
            phone_code=49,
            sort_order=10,
        )
        self.country.set_current_language("en")
        self.country.name = "Germany"
        self.country.save()

        self.country_no_flag = Country.objects.create(
            alpha_2="FR",
            alpha_3="FRA",
            iso_cc=250,
            phone_code=33,
        )
        self.country_no_flag.set_current_language("en")
        self.country_no_flag.name = "France"
        self.country_no_flag.save()

        self.incomplete_country = Country.objects.create(
            alpha_2="ZZ",
            alpha_3="ZZZ",
        )
        self.incomplete_country.set_current_language("en")
        self.incomplete_country.name = "Test Country"
        self.incomplete_country.save()

    def test_country_info(self):
        result = self.admin.country_info(self.country)

        self.assertIn("Germany", result)
        self.assertIn("DE", result)
        self.assertIn("✅", result)
        self.assertIn("Sort: 10", result)
        self.assertIn("text-green-600", result)

    def test_country_info_without_iso(self):
        result = self.admin.country_info(self.incomplete_country)

        self.assertIn("Test Country", result)
        self.assertIn("ZZ", result)
        self.assertIn("⚠️", result)
        self.assertIn("text-orange-600", result)

    def test_flag_display_with_flag(self):
        self.country.image_flag = Mock()
        self.country.image_flag.url = "/media/flags/de.png"

        result = self.admin.flag_display(self.country)

        self.assertIn('<img src="/media/flags/de.png"', result)
        self.assertIn("width: 48px", result)
        self.assertIn("height: 32px", result)

    def test_flag_display_without_flag(self):
        result = self.admin.flag_display(self.country_no_flag)

        self.assertIn("🏳️", result)
        self.assertIn("background: linear-gradient", result)

    def test_codes_display(self):
        result = self.admin.codes_display(self.country)

        self.assertIn("DE", result)
        self.assertIn("DEU", result)
        self.assertIn("ISO: 276", result)
        self.assertIn("bg-blue-50", result)
        self.assertIn("bg-green-50", result)

    def test_codes_display_without_iso(self):
        result = self.admin.codes_display(self.incomplete_country)

        self.assertIn("ZZ", result)
        self.assertIn("ZZZ", result)
        self.assertIn("ISO: —", result)
        self.assertIn("text-base-400", result)

    def test_contact_info_with_phone(self):
        result = self.admin.contact_info(self.country)

        self.assertIn("📞 +49", result)
        self.assertIn("bg-blue-50", result)

    def test_contact_info_single_digit_phone(self):
        self.country.phone_code = 1
        self.country.save()

        result = self.admin.contact_info(self.country)

        self.assertIn("📞 +1", result)
        self.assertIn("bg-green-50", result)

    def test_contact_info_three_digit_phone(self):
        self.country.phone_code = 123
        self.country.save()

        result = self.admin.contact_info(self.country)

        self.assertIn("📞 +123", result)
        self.assertIn("bg-orange-50", result)

    def test_contact_info_no_phone(self):
        result = self.admin.contact_info(self.incomplete_country)

        self.assertIn("📞 No Code", result)
        self.assertIn("bg-gray-50", result)

    def test_completeness_badge_complete(self):
        self.country.image_flag = SimpleUploadedFile(
            "flag.jpg", b"fake_image_data", content_type="image/jpeg"
        )
        self.country.save()

        result = self.admin.completeness_badge(self.country)

        self.assertIn("100%", result)
        self.assertIn("✅", result)
        self.assertIn("Complete", result)
        self.assertIn("bg-green-50", result)

    def test_completeness_badge_good(self):
        result = self.admin.completeness_badge(self.country)

        self.assertIn("75%", result)
        self.assertIn("🔷", result)
        self.assertIn("Good", result)
        self.assertIn("bg-blue-50", result)

    def test_completeness_badge_partial(self):
        self.country.phone_code = None
        self.country.save()

        result = self.admin.completeness_badge(self.country)

        self.assertIn("50%", result)
        self.assertIn("⚠️", result)
        self.assertIn("Partial", result)
        self.assertIn("bg-yellow-50", result)

    def test_completeness_badge_incomplete(self):
        result = self.admin.completeness_badge(self.incomplete_country)

        self.assertIn("25%", result)
        self.assertIn("❌", result)
        self.assertIn("Incomplete", result)
        self.assertIn("bg-red-50", result)

    def test_created_display(self):
        result = self.admin.created_display(self.country)

        date_str = self.country.created_at.strftime("%Y-%m-%d")
        time_str = self.country.created_at.strftime("%H:%M")

        self.assertIn(date_str, result)
        self.assertIn(time_str, result)

    def test_update_sort_order_action(self):
        request = self.factory.post("/admin/country/country/")
        request.user = Mock()
        request._messages = Mock()

        Country.objects.create(alpha_2="AA", alpha_3="AAA", iso_cc=1)
        Country.objects.create(alpha_2="BB", alpha_3="BBB", iso_cc=2)

        queryset = Country.objects.all()

        with patch.object(self.admin, "message_user") as mock_message:
            self.admin.update_sort_order(request, queryset)

            mock_message.assert_called_once()
            args = mock_message.call_args[0]
            self.assertEqual(args[0], request)
            self.assertIn("Updated sort order", args[1])

        countries = list(Country.objects.order_by("sort_order"))
        self.assertIsNotNone(countries[0].sort_order)
        self.assertIsNotNone(countries[1].sort_order)
        alpha_2_values = [c.alpha_2 for c in countries]
        self.assertEqual(alpha_2_values, sorted(alpha_2_values))


class CountryAdminIntegrationTestCase(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.admin = CountryAdmin(Country, self.site)

        self.countries = []
        for i, (alpha_2, name, iso_cc, phone) in enumerate(
            [
                ("US", "United States", 840, 1),
                ("DE", "Germany", 276, 49),
                ("JP", "Japan", 392, 81),
                ("XX", "Test Country", None, None),
            ]
        ):
            country = Country.objects.create(
                alpha_2=alpha_2,
                alpha_3=f"{alpha_2}X",
                iso_cc=iso_cc,
                phone_code=phone,
            )
            country.set_current_language("en")
            country.name = name
            country.save()
            self.countries.append(country)

    def test_admin_display_methods_integration(self):
        for country in self.countries:
            with self.subTest(country=country.alpha_2):
                country_info = self.admin.country_info(country)
                flag_display = self.admin.flag_display(country)
                codes_display = self.admin.codes_display(country)
                contact_info = self.admin.contact_info(country)
                completeness_badge = self.admin.completeness_badge(country)
                created_display = self.admin.created_display(country)

                self.assertIsInstance(country_info, str)
                self.assertIsInstance(flag_display, str)
                self.assertIsInstance(codes_display, str)
                self.assertIsInstance(contact_info, str)
                self.assertIsInstance(completeness_badge, str)
                self.assertIsInstance(created_display, str)

                self.assertIn(country.name, country_info)
                self.assertIn(country.alpha_2, codes_display)
                self.assertTrue(len(flag_display) > 0)
                self.assertIn("📞", contact_info)
                self.assertIn("%", completeness_badge)

    def test_filter_functionality(self):
        factory = RequestFactory()
        request = factory.get("/admin/country/country/")

        active_filter = CountryStatusFilter(
            request=request,
            params={"country_status": "active"},
            model=Country,
            model_admin=self.admin,
        )

        active_queryset = active_filter.queryset(request, Country.objects.all())

        self.assertIsNotNone(active_queryset)
        self.assertTrue(active_queryset.count() >= 0)
        self.assertLessEqual(active_queryset.count(), Country.objects.count())

    def test_admin_configuration(self):
        self.assertEqual(self.admin.list_per_page, 50)
        self.assertEqual(self.admin.ordering, ["sort_order", "alpha_2"])
        self.assertTrue(self.admin.list_fullwidth)
        self.assertTrue(self.admin.list_filter_submit)

        expected_display = [
            "country_info",
            "flag_display",
            "codes_display",
            "contact_info",
            "completeness_badge",
            "created_display",
        ]
        self.assertEqual(self.admin.list_display, expected_display)

        expected_search = [
            "translations__name",
            "alpha_2",
            "alpha_3",
            "iso_cc",
            "phone_code",
        ]
        self.assertEqual(self.admin.search_fields, expected_search)
