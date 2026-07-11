import datetime
from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from django.utils.translation import gettext
from unfold.contrib.filters.admin import RangeDateTimeFilter
from zoneinfo import ZoneInfo

from contact.admin import ContactAdmin, MessageLengthFilter, RecentContactFilter
from contact.models import Contact

pytestmark = pytest.mark.assert_english


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
        self.assertEqual(str(filter_instance.title), gettext("Message Length"))
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
        self.week_contact.created_at = now - datetime.timedelta(days=3)
        self.week_contact.save()

        self.month_contact = Contact.objects.create(
            name="Month User",
            email="month@example.com",
            message="Month message",
        )
        self.month_contact.created_at = now - datetime.timedelta(days=15)
        self.month_contact.save()

        self.quarter_contact = Contact.objects.create(
            name="Quarter User",
            email="quarter@example.com",
            message="Quarter message",
        )
        self.quarter_contact.created_at = now - datetime.timedelta(days=60)
        self.quarter_contact.save()

        self.old_contact = Contact.objects.create(
            name="Old User", email="old@example.com", message="Old message"
        )
        self.old_contact.created_at = now - datetime.timedelta(days=200)
        self.old_contact.save()

    def test_filter_title_and_parameter(self):
        filter_instance = RecentContactFilter(
            self.request, {}, Contact, self.model_admin
        )
        self.assertEqual(str(filter_instance.title), gettext("Contact Period"))
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
            "priority",
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
        )
        self.assertEqual(self.admin.readonly_fields, expected_fields)

    def test_get_ordering(self):
        ordering = self.admin.get_ordering(self.request)
        expected = ["-created_at", "name"]
        self.assertEqual(ordering, expected)

    def test_contact_info_normal_email(self):
        primary, secondary, _initials = self.admin.contact_info(self.contact)

        self.assertEqual(primary, "John Doe Test User")
        self.assertEqual(secondary, "john.doe@example.com")

    def test_contact_info_suspicious_email(self):
        suspicious_contact = Contact.objects.create(
            name="Suspicious User", email="invalidemail", message="Test message"
        )

        _primary, secondary, _initials = self.admin.contact_info(
            suspicious_contact
        )

        self.assertIn("invalidemail", secondary)
        self.assertIn("invalid", secondary)

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

    def test_message_stats(self):
        result = self.admin.message_stats(self.contact)

        self.assertIn("chars", result)
        self.assertIn("words", result)
        self.assertIn("lines", result)

    def test_contact_timing(self):
        # ``contact_timing`` delegates the relative-time portion to the
        # shared ``admin.displays.relative_time`` helper, which resolves
        # ``timezone.now()`` internally — so this asserts against the
        # real clock rather than mocking ``contact.admin.timezone``.
        self.contact.created_at = timezone.now() - datetime.timedelta(hours=3)
        self.contact.save()

        result = self.admin.contact_timing(self.contact)
        self.assertIn(self.contact.created_at.strftime("%d/%m/%Y"), result)
        self.assertIn("ω", result)

    @patch("contact.admin.timezone")
    def test_priority_urgent(self, mock_timezone):
        now = datetime.datetime(2023, 12, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC"))
        mock_timezone.now.return_value = now

        urgent_contact = Contact.objects.create(
            name="Urgent User",
            email="urgent@example.com",
            message="URGENT: Need immediate help with billing issue!",
        )
        urgent_contact.created_at = now - datetime.timedelta(minutes=5)
        urgent_contact.save()

        value, label = self.admin.priority(urgent_contact)
        self.assertEqual(value, "urgent")
        self.assertEqual(label, "Urgent")

    @patch("contact.admin.timezone")
    def test_priority_high(self, mock_timezone):
        now = datetime.datetime(2023, 12, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC"))
        mock_timezone.now.return_value = now

        high_contact = Contact.objects.create(
            name="High User",
            email="high@example.com",
            message="A" * 600,
        )
        high_contact.created_at = now - datetime.timedelta(hours=2)
        high_contact.save()

        value, label = self.admin.priority(high_contact)
        self.assertEqual(value, "high")
        self.assertEqual(label, "High")

    @patch("contact.admin.timezone")
    def test_priority_medium(self, mock_timezone):
        now = datetime.datetime(2023, 12, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC"))
        mock_timezone.now.return_value = now

        medium_contact = Contact.objects.create(
            name="Medium User",
            email="medium@example.com",
            message="General inquiry about services",
        )
        medium_contact.created_at = now - datetime.timedelta(hours=12)
        medium_contact.save()

        value, label = self.admin.priority(medium_contact)
        self.assertEqual(value, "medium")
        self.assertEqual(label, "Medium")

    @patch("contact.admin.timezone")
    def test_priority_low(self, mock_timezone):
        now = datetime.datetime(2023, 12, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC"))
        mock_timezone.now.return_value = now

        low_contact = Contact.objects.create(
            name="Low User",
            email="low@example.com",
            message="Just wanted to say thanks",
        )
        low_contact.created_at = now - datetime.timedelta(days=10)
        low_contact.save()

        value, label = self.admin.priority(low_contact)
        self.assertEqual(value, "low")
        self.assertEqual(label, "Low")

    def test_method_short_descriptions(self):
        self.assertEqual(
            str(self.admin.contact_info.short_description), gettext("Contact")
        )
        self.assertEqual(
            str(self.admin.message_preview.short_description),
            gettext("Message"),
        )
        self.assertEqual(
            str(self.admin.message_stats.short_description),
            gettext("Message Stats"),
        )
        self.assertEqual(
            str(self.admin.contact_timing.short_description), gettext("Timing")
        )
        self.assertEqual(
            str(self.admin.priority.short_description),
            gettext("Priority"),
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
        self.assertEqual(str(basic_fieldset[0]), gettext("Contact Information"))

        basic_fields = basic_fieldset[1]["fields"]
        self.assertIn("name", basic_fields)
        self.assertIn("email", basic_fields)

        message_fieldset = fieldsets[1]
        self.assertEqual(str(message_fieldset[0]), gettext("Message"))
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

        primary, secondary, _initials = self.admin.contact_info(
            empty_email_contact
        )

        self.assertEqual(primary, "No Email User")
        self.assertIn("(no email)", secondary)

    def test_contact_info_extremely_long_name(self):
        long_name = "A" * 90
        long_name_contact = Contact.objects.create(
            name=long_name, email="longname@example.com", message="Test message"
        )

        primary, _secondary, _initials = self.admin.contact_info(
            long_name_contact
        )

        self.assertEqual(primary, long_name)

    def test_message_preview_html_is_escaped_on_render(self):
        # ``message_preview`` returns a plain (unescaped) string — Django's
        # admin changelist template autoescapes it at render time, same as
        # any other non-``format_html``/``mark_safe`` display method.
        html_contact = Contact.objects.create(
            name="HTML User",
            email="html@example.com",
            message="<script>alert('test')</script><b>Bold text</b>",
        )

        result = self.admin.message_preview(html_contact)

        self.assertIn("<script>", result)

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
            self.admin.priority(zero_contact)
        except ZeroDivisionError:
            self.fail(
                "Methods should handle zero values without ZeroDivisionError"
            )
