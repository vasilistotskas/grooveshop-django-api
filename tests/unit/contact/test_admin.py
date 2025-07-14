import datetime
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.db import models
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from unfold.contrib.filters.admin import RangeDateTimeFilter
from zoneinfo import ZoneInfo

from contact.admin import ContactAdmin, MessageLengthFilter, RecentContactFilter
from contact.models import Contact


class TestMessageLengthFilter(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.user = User(username="testuser")

        class MockModelAdmin:
            pass

        self.model_admin = MockModelAdmin()

        self.short_contact = Contact.objects.create(
            name="Short User",
            email="short@example.com",
            message="Short message",
        )

        self.medium_contact = Contact.objects.create(
            name="Medium User",
            email="medium@example.com",
            message="A" * 200,
        )

        self.long_contact = Contact.objects.create(
            name="Long User",
            email="long@example.com",
            message="A" * 600,
        )

    def test_filter_title_and_parameter(self):
        filter_instance = MessageLengthFilter(
            self.request, {}, Contact, self.model_admin
        )
        self.assertEqual(filter_instance.title, "Message Length")
        self.assertEqual(filter_instance.parameter_name, "message_length")

    def test_lookups(self):
        filter_instance = MessageLengthFilter(
            self.request, {}, Contact, self.model_admin
        )

        lookups = filter_instance.lookups(self.request, None)
        expected_lookups = [
            ("short", "Short (<100 chars)"),
            ("medium", "Medium (100-500 chars)"),
            ("long", "Long (>500 chars)"),
        ]

        self.assertEqual(list(lookups), expected_lookups)

    def test_queryset_short_filter(self):
        request = self.factory.get("/", {"message_length": "short"})
        request.user = self.request.user
        filter_instance = MessageLengthFilter(
            request, request.GET.copy(), Contact, self.model_admin
        )

        queryset = Contact.objects.all()
        filtered_queryset = filter_instance.queryset(request, queryset)

        self.assertIn(self.short_contact, filtered_queryset)
        self.assertNotIn(self.medium_contact, filtered_queryset)
        self.assertNotIn(self.long_contact, filtered_queryset)

    def test_queryset_medium_filter(self):
        request = self.factory.get("/", {"message_length": "medium"})
        request.user = self.request.user
        filter_instance = MessageLengthFilter(
            request, request.GET.copy(), Contact, self.model_admin
        )

        queryset = Contact.objects.all()
        filtered_queryset = filter_instance.queryset(request, queryset)

        self.assertNotIn(self.short_contact, filtered_queryset)
        self.assertIn(self.medium_contact, filtered_queryset)
        self.assertNotIn(self.long_contact, filtered_queryset)

    def test_queryset_long_filter(self):
        request = self.factory.get("/", {"message_length": "long"})
        request.user = self.request.user

        filter_instance = MessageLengthFilter(
            request, request.GET.copy(), Contact, self.model_admin
        )

        queryset = Contact.objects.all()
        filtered_queryset = filter_instance.queryset(request, queryset)

        self.assertNotIn(self.short_contact, filtered_queryset)
        self.assertNotIn(self.medium_contact, filtered_queryset)
        self.assertIn(self.long_contact, filtered_queryset)

    def test_queryset_no_filter(self):
        filter_instance = MessageLengthFilter(
            self.request, {}, Contact, self.model_admin
        )

        queryset = Contact.objects.all()
        filtered_queryset = filter_instance.queryset(self.request, queryset)

        self.assertEqual(list(queryset), list(filtered_queryset))

    def test_queryset_invalid_filter(self):
        request = self.factory.get("/", {"message_length": "invalid"})
        request.user = self.request.user
        filter_instance = MessageLengthFilter(
            request, request.GET.copy(), Contact, self.model_admin
        )

        queryset = Contact.objects.all()
        filtered_queryset = filter_instance.queryset(request, queryset)

        self.assertEqual(list(queryset), list(filtered_queryset))


@override_settings(LANGUAGE_CODE="en-US")
class TestRecentContactFilter(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.user = User(username="testuser")

        class MockModelAdmin:
            pass

        self.model_admin = MockModelAdmin()

        now = timezone.now()

        self.today_contact = Contact.objects.create(
            name="Today User",
            email="today@example.com",
            message="Today message",
        )
        self.today_contact.created_at = now
        self.today_contact.save()

        self.week_contact = Contact.objects.create(
            name="Week User", email="week@example.com", message="Week message"
        )
        self.week_contact.created_at = now - timezone.timedelta(days=3)
        self.week_contact.save()

        self.month_contact = Contact.objects.create(
            name="Month User",
            email="month@example.com",
            message="Month message",
        )
        self.month_contact.created_at = now - timezone.timedelta(days=15)
        self.month_contact.save()

        self.quarter_contact = Contact.objects.create(
            name="Quarter User",
            email="quarter@example.com",
            message="Quarter message",
        )
        self.quarter_contact.created_at = now - timezone.timedelta(days=60)
        self.quarter_contact.save()

        self.old_contact = Contact.objects.create(
            name="Old User", email="old@example.com", message="Old message"
        )
        self.old_contact.created_at = now - timezone.timedelta(days=200)
        self.old_contact.save()

    def test_filter_title_and_parameter(self):
        filter_instance = RecentContactFilter(
            self.request, {}, Contact, self.model_admin
        )
        self.assertEqual(filter_instance.title, "Contact Period")
        self.assertEqual(filter_instance.parameter_name, "contact_period")

    def test_lookups(self):
        filter_instance = RecentContactFilter(
            self.request, {}, Contact, self.model_admin
        )

        lookups = filter_instance.lookups(self.request, None)
        expected_lookups = [
            ("today", "Today"),
            ("week", "This Week"),
            ("month", "This Month"),
            ("quarter", "This Quarter"),
        ]

        self.assertEqual(list(lookups), expected_lookups)

    @patch("contact.admin.timezone")
    def test_queryset_today_filter(self, mock_timezone):
        mock_now = datetime.datetime(
            2023, 12, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC")
        )
        mock_timezone.now.return_value = mock_now

        filter_instance = RecentContactFilter(
            self.request, {"contact_period": "today"}, Contact, self.model_admin
        )

        queryset = Contact.objects.all()
        filter_instance.queryset(self.request, queryset)

        mock_timezone.now.assert_called()

    @patch("contact.admin.timezone")
    def test_queryset_week_filter(self, mock_timezone):
        mock_now = datetime.datetime(
            2023, 12, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC")
        )
        mock_timezone.now.return_value = mock_now

        filter_instance = RecentContactFilter(
            self.request, {"contact_period": "week"}, Contact, self.model_admin
        )

        queryset = Contact.objects.all()
        filter_instance.queryset(self.request, queryset)

        mock_timezone.now.assert_called()

    @patch("contact.admin.timezone")
    def test_queryset_month_filter(self, mock_timezone):
        mock_now = datetime.datetime(
            2023, 12, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC")
        )
        mock_timezone.now.return_value = mock_now

        filter_instance = RecentContactFilter(
            self.request, {"contact_period": "month"}, Contact, self.model_admin
        )

        queryset = Contact.objects.all()
        filter_instance.queryset(self.request, queryset)

        mock_timezone.now.assert_called()

    @patch("contact.admin.timezone")
    def test_queryset_quarter_filter(self, mock_timezone):
        mock_now = datetime.datetime(
            2023, 12, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC")
        )
        mock_timezone.now.return_value = mock_now

        filter_instance = RecentContactFilter(
            self.request,
            {"contact_period": "quarter"},
            Contact,
            self.model_admin,
        )

        queryset = Contact.objects.all()
        filter_instance.queryset(self.request, queryset)

        mock_timezone.now.assert_called()

    def test_queryset_no_filter(self):
        filter_instance = RecentContactFilter(
            self.request, {}, Contact, self.model_admin
        )

        queryset = Contact.objects.all()
        filtered_queryset = filter_instance.queryset(self.request, queryset)

        self.assertEqual(list(queryset), list(filtered_queryset))


class TestContactAdmin(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.user = User(username="testuser")

        self.site = AdminSite()
        self.admin = ContactAdmin(Contact, self.site)

        self.contact = Contact.objects.create(
            name="John Doe Test User",
            email="john.doe@example.com",
            message="This is a test message with some content to analyze.",
        )

    def test_admin_configuration(self):
        self.assertTrue(self.admin.compressed_fields)
        self.assertTrue(self.admin.warn_unsaved_form)
        self.assertTrue(self.admin.list_fullwidth)
        self.assertTrue(self.admin.list_filter_submit)
        self.assertTrue(self.admin.list_filter_sheet)
        self.assertEqual(self.admin.list_per_page, 25)
        self.assertEqual(self.admin.date_hierarchy, "created_at")

    def test_list_display(self):
        expected_display = [
            "contact_info",
            "message_preview",
            "message_stats",
            "contact_timing",
            "priority_badge",
        ]
        self.assertEqual(self.admin.list_display, expected_display)

    def test_search_fields(self):
        expected_fields = ["name", "email", "message"]
        self.assertEqual(self.admin.search_fields, expected_fields)

    def test_readonly_fields(self):
        expected_fields = (
            "id",
            "uuid",
            "created_at",
            "updated_at",
            "contact_analytics",
            "message_analytics",
            "timing_info",
        )
        self.assertEqual(self.admin.readonly_fields, expected_fields)

    def test_get_queryset(self):
        queryset = self.admin.get_queryset(self.request)
        self.assertIsInstance(queryset, models.QuerySet)
        self.assertIn(self.contact, queryset)

    def test_get_ordering(self):
        ordering = self.admin.get_ordering(self.request)
        expected = ["-created_at", "name"]
        self.assertEqual(ordering, expected)

    def test_contact_info_normal_email(self):
        result = self.admin.contact_info(self.contact)

        self.assertIn("John Doe Test User", result)
        self.assertIn("john.doe@example.com", result)
        self.assertIn(str(self.contact.id), result)
        self.assertIn("text-base-600", result)

    def test_contact_info_suspicious_email(self):
        suspicious_contact = Contact.objects.create(
            name="Suspicious User", email="invalidemail", message="Test message"
        )

        result = self.admin.contact_info(suspicious_contact)

        self.assertIn("text-red-600", result)
        self.assertIn("invalidemail", result)

    def test_contact_info_long_name(self):
        long_name_contact = Contact.objects.create(
            name="A" * 50,
            email="test@example.com",
            message="Test message",
        )

        result = self.admin.contact_info(long_name_contact)

        self.assertIn("...", result)
        self.assertNotIn("A" * 50, result)

    def test_message_preview_short_message(self):
        result = self.admin.message_preview(self.contact)

        self.assertIn("This is a test message", result)
        self.assertNotIn("...", result)

    def test_message_preview_long_message(self):
        long_message_contact = Contact.objects.create(
            name="Long Message User",
            email="long@example.com",
            message="A" * 150,
        )

        result = self.admin.message_preview(long_message_contact)

        self.assertIn("...", result)

    def test_message_preview_newlines(self):
        newline_contact = Contact.objects.create(
            name="Newline User",
            email="newline@example.com",
            message="Line 1\nLine 2\nLine 3",
        )

        result = self.admin.message_preview(newline_contact)

        self.assertIn("Line 1 Line 2 Line 3", result)

    def test_message_stats_short_message(self):
        result = self.admin.message_stats(self.contact)

        self.assertIn("text-green-700", result)
        self.assertIn("chars", result)
        self.assertIn("words", result)

    def test_message_stats_medium_message(self):
        medium_contact = Contact.objects.create(
            name="Medium User",
            email="medium@example.com",
            message="A" * 300,
        )

        result = self.admin.message_stats(medium_contact)

        self.assertIn("text-yellow-700", result)

    def test_message_stats_long_message(self):
        long_contact = Contact.objects.create(
            name="Long User",
            email="long@example.com",
            message="A" * 600,
        )

        result = self.admin.message_stats(long_contact)

        self.assertIn("text-blue-700", result)

    @patch("contact.admin.timezone")
    def test_contact_timing_just_now(self, mock_timezone):
        now = datetime.datetime(2023, 12, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC"))
        mock_timezone.now.return_value = now

        self.contact.created_at = now - timezone.timedelta(minutes=5)
        self.contact.save()

        result = self.admin.contact_timing(self.contact)
        self.assertIn("Just Now", result)
        self.assertIn("text-red-700", result)

    @patch("contact.admin.timezone")
    def test_contact_timing_hours_ago(self, mock_timezone):
        now = datetime.datetime(2023, 12, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC"))
        mock_timezone.now.return_value = now

        self.contact.created_at = now - timezone.timedelta(hours=3)
        self.contact.save()

        result = self.admin.contact_timing(self.contact)
        self.assertIn("3h ago", result)
        self.assertIn("text-orange-700", result)

    @patch("contact.admin.timezone")
    def test_contact_timing_days_ago(self, mock_timezone):
        now = datetime.datetime(2023, 12, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC"))
        mock_timezone.now.return_value = now

        self.contact.created_at = now - timezone.timedelta(days=2)
        self.contact.save()

        result = self.admin.contact_timing(self.contact)
        self.assertIn("2d ago", result)

    @patch("contact.admin.timezone")
    def test_contact_timing_old(self, mock_timezone):
        now = datetime.datetime(2023, 12, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC"))
        mock_timezone.now.return_value = now

        self.contact.created_at = now - timezone.timedelta(days=10)
        self.contact.save()

        result = self.admin.contact_timing(self.contact)
        self.assertIn("Old", result)
        self.assertIn("text-base-700", result)

    @patch("contact.admin.timezone")
    def test_priority_badge_urgent(self, mock_timezone):
        now = datetime.datetime(2023, 12, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC"))
        mock_timezone.now.return_value = now

        urgent_contact = Contact.objects.create(
            name="Urgent User",
            email="urgent@example.com",
            message="URGENT: Need immediate help with billing issue!",
        )
        urgent_contact.created_at = now - timezone.timedelta(minutes=5)
        urgent_contact.save()

        result = self.admin.priority_badge(urgent_contact)
        self.assertIn("Urgent", result)
        self.assertIn("bg-red-50", result)

    @patch("contact.admin.timezone")
    def test_priority_badge_high(self, mock_timezone):
        now = datetime.datetime(2023, 12, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC"))
        mock_timezone.now.return_value = now

        high_contact = Contact.objects.create(
            name="High User",
            email="high@example.com",
            message="A" * 600,
        )
        high_contact.created_at = now - timezone.timedelta(hours=2)
        high_contact.save()

        result = self.admin.priority_badge(high_contact)
        self.assertIn("High", result)
        self.assertIn("bg-orange-50", result)

    @patch("contact.admin.timezone")
    def test_priority_badge_medium(self, mock_timezone):
        now = datetime.datetime(2023, 12, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC"))
        mock_timezone.now.return_value = now

        medium_contact = Contact.objects.create(
            name="Medium User",
            email="medium@example.com",
            message="General inquiry about services",
        )
        medium_contact.created_at = now - timezone.timedelta(hours=12)
        medium_contact.save()

        result = self.admin.priority_badge(medium_contact)
        self.assertIn("Medium", result)
        self.assertIn("bg-yellow-50", result)

    @patch("contact.admin.timezone")
    def test_priority_badge_low(self, mock_timezone):
        now = datetime.datetime(2023, 12, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC"))
        mock_timezone.now.return_value = now

        low_contact = Contact.objects.create(
            name="Low User",
            email="low@example.com",
            message="Just wanted to say thanks",
        )
        low_contact.created_at = now - timezone.timedelta(days=10)
        low_contact.save()

        result = self.admin.priority_badge(low_contact)
        self.assertIn("Low", result)
        self.assertIn("bg-green-50", result)

    @patch("contact.admin.timezone")
    def test_contact_analytics(self, mock_timezone):
        now = datetime.datetime(2023, 12, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC"))
        mock_timezone.now.return_value = now

        self.contact.created_at = now - timezone.timedelta(hours=3)
        self.contact.save()

        result = self.admin.contact_analytics(self.contact)

        self.assertIn("Response Time", result)
        self.assertIn("Priority Score", result)

    def test_message_analytics(self):
        result = self.admin.message_analytics(self.contact)

        self.assertIn("Reading Time", result)
        self.assertIn("Sentiment", result)
        self.assertIn("Complexity", result)
        self.assertIn("Language", result)

        self.assertIn("seconds", result)
        self.assertIn("Neutral", result)
        self.assertIn("English", result)

    def test_message_analytics_urgent_words(self):
        urgent_contact = Contact.objects.create(
            name="Urgent User",
            email="urgent@example.com",
            message="URGENT help needed ASAP emergency situation!",
        )

        result = self.admin.message_analytics(urgent_contact)

        self.assertIn("Urgent", result)

    @patch("contact.admin.timezone")
    def test_timing_info(self, mock_timezone):
        now = datetime.datetime(2023, 12, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC"))
        mock_timezone.now.return_value = now

        created_time = datetime.datetime(
            2023, 12, 13, 10, 15, 0, tzinfo=ZoneInfo("UTC")
        )
        self.contact.created_at = created_time
        self.contact.save()

        result = self.admin.timing_info(self.contact)

        self.assertIn("Age", result)
        self.assertIn("Business Hours", result)
        self.assertIn("Season", result)

        self.assertIn("Yes", result)

    def test_timing_info_business_hours(self):
        business_time = datetime.datetime(
            2023, 12, 13, 10, 0, 0, tzinfo=ZoneInfo("UTC")
        )
        self.contact.created_at = business_time
        self.contact.save()

        result = self.admin.timing_info(self.contact)
        self.assertIn("Yes", result)

    def test_timing_info_weekend(self):
        weekend_time = datetime.datetime(
            2023, 12, 16, 10, 0, 0, tzinfo=ZoneInfo("UTC")
        )
        self.contact.created_at = weekend_time
        self.contact.save()

        result = self.admin.timing_info(self.contact)
        self.assertIn("Weekend Contact", result)

    def test_get_season_winter(self):
        winter_date = datetime.date(2023, 1, 15)
        season = self.admin._get_season(winter_date)
        self.assertEqual(season, "Winter")

    def test_get_season_spring(self):
        spring_date = datetime.date(2023, 4, 15)
        season = self.admin._get_season(spring_date)
        self.assertEqual(season, "Spring")

    def test_get_season_summer(self):
        summer_date = datetime.date(2023, 7, 15)
        season = self.admin._get_season(summer_date)
        self.assertEqual(season, "Summer")

    def test_get_season_autumn(self):
        autumn_date = datetime.date(2023, 10, 15)
        season = self.admin._get_season(autumn_date)
        self.assertEqual(season, "Autumn")

    def test_method_short_descriptions(self):
        self.assertEqual(self.admin.contact_info.short_description, "Contact")
        self.assertEqual(
            self.admin.message_preview.short_description, "Message"
        )
        self.assertEqual(
            self.admin.message_stats.short_description, "Message Stats"
        )
        self.assertEqual(self.admin.contact_timing.short_description, "Timing")
        self.assertEqual(
            self.admin.priority_badge.short_description, "Priority"
        )
        self.assertEqual(
            self.admin.contact_analytics.short_description, "Contact Analytics"
        )
        self.assertEqual(
            self.admin.message_analytics.short_description, "Message Analytics"
        )
        self.assertEqual(
            self.admin.timing_info.short_description, "Timing Information"
        )


class TestContactAdminIntegration(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.user = User(username="testuser")

        self.site = AdminSite()
        self.admin = ContactAdmin(Contact, self.site)

    def test_fieldsets_structure(self):
        fieldsets = self.admin.fieldsets
        self.assertIsInstance(fieldsets, tuple)

        self.assertGreater(len(fieldsets), 1)

        basic_fieldset = fieldsets[0]
        self.assertEqual(basic_fieldset[0], "Contact Information")

        basic_fields = basic_fieldset[1]["fields"]
        self.assertIn("name", basic_fields)
        self.assertIn("email", basic_fields)

        message_fieldset = fieldsets[1]
        self.assertEqual(message_fieldset[0], "Message")
        message_fields = message_fieldset[1]["fields"]
        self.assertIn("message", message_fields)

    def test_list_filter_configuration(self):
        expected_filters = [
            RecentContactFilter,
            MessageLengthFilter,
            ("created_at", RangeDateTimeFilter),
            ("updated_at", RangeDateTimeFilter),
        ]
        self.assertEqual(self.admin.list_filter, expected_filters)

    def test_admin_registered(self):
        from django.contrib import admin

        self.assertIn(Contact, admin.site._registry)

        registered_admin = admin.site._registry[Contact]
        self.assertIsInstance(registered_admin, ContactAdmin)

    def test_unfold_integration(self):
        self.assertTrue(hasattr(self.admin, "compressed_fields"))
        self.assertTrue(hasattr(self.admin, "warn_unsaved_form"))


class TestContactAdminEdgeCases(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/")
        self.request.user = User(username="testuser")

        self.site = AdminSite()
        self.admin = ContactAdmin(Contact, self.site)

    def test_contact_info_empty_email(self):
        empty_email_contact = Contact.objects.create(
            name="No Email User", email="", message="Test message"
        )

        result = self.admin.contact_info(empty_email_contact)

        self.assertIn("No Email User", result)
        self.assertIn("(no email)", result)

    def test_message_analytics_empty_message(self):
        empty_message_contact = Contact.objects.create(
            name="Empty Message User", email="empty@example.com", message=""
        )

        result = self.admin.message_analytics(empty_message_contact)

        self.assertIn("Reading Time", result)
        self.assertIn("0 seconds", result)

    def test_message_analytics_special_characters(self):
        special_contact = Contact.objects.create(
            name="Special User",
            email="special@example.com",
            message="Message with émojis 😊 and spëcial chàracters!",
        )

        result = self.admin.message_analytics(special_contact)

        self.assertIn("Reading Time", result)
        self.assertIn("Complexity", result)

    def test_timing_info_edge_hours(self):
        edge_time = datetime.datetime(
            2023, 12, 13, 9, 0, 0, tzinfo=ZoneInfo("UTC")
        )
        self.contact = Contact.objects.create(
            name="Edge User",
            email="edge@example.com",
            message="Edge case message",
        )
        self.contact.created_at = edge_time
        self.contact.save()

        result = self.admin.timing_info(self.contact)

        self.assertIn("09:00", result)
        self.assertIn("Yes", result)

    def test_contact_info_extremely_long_name(self):
        long_name = "A" * 90
        long_name_contact = Contact.objects.create(
            name=long_name, email="longname@example.com", message="Test message"
        )

        result = self.admin.contact_info(long_name_contact)

        self.assertIn("...", result)
        self.assertNotIn("A" * 90, result)
        self.assertIn("<div", result)
        self.assertIn("</div>", result)

    def test_message_preview_html_injection(self):
        html_contact = Contact.objects.create(
            name="HTML User",
            email="html@example.com",
            message="<script>alert('test')</script><b>Bold text</b>",
        )

        result = self.admin.message_preview(html_contact)

        self.assertNotIn("<script>", result)
        self.assertNotIn("<b>", result)
        self.assertIn("&lt;", result)

    def test_division_by_zero_protection(self):
        zero_contact = Contact.objects.create(
            name="",
            email="zero@example.com",
            message="",
        )

        try:
            self.admin.contact_info(zero_contact)
            self.admin.message_preview(zero_contact)
            self.admin.message_stats(zero_contact)
            self.admin.contact_timing(zero_contact)
            self.admin.priority_badge(zero_contact)
            self.admin.contact_analytics(zero_contact)
            self.admin.message_analytics(zero_contact)
            self.admin.timing_info(zero_contact)
        except ZeroDivisionError:
            self.fail(
                "Methods should handle zero values without ZeroDivisionError"
            )
