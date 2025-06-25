from datetime import timedelta

from django.contrib import admin
from django.db.models.functions import Length
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import (
    RangeDateTimeFilter,
)

from contact.models import Contact
from core.admin import ExportModelAdmin


class MessageLengthFilter(admin.SimpleListFilter):
    title = _("Message Length")
    parameter_name = "message_length"

    def lookups(self, request, model_admin):
        return [
            ("short", _("Short (<100 chars)")),
            ("medium", _("Medium (100-500 chars)")),
            ("long", _("Long (>500 chars)")),
        ]

    def queryset(self, request, queryset):
        if self.value() == "short":
            return queryset.annotate(msg_len=Length("message")).filter(
                msg_len__lt=100
            )
        elif self.value() == "medium":
            return queryset.annotate(msg_len=Length("message")).filter(
                msg_len__range=(100, 500)
            )
        elif self.value() == "long":
            return queryset.annotate(msg_len=Length("message")).filter(
                msg_len__gt=500
            )
        return queryset


class RecentContactFilter(admin.SimpleListFilter):
    title = _("Contact Period")
    parameter_name = "contact_period"

    def lookups(self, request, model_admin):
        return [
            ("today", _("Today")),
            ("week", _("This Week")),
            ("month", _("This Month")),
            ("quarter", _("This Quarter")),
        ]

    def queryset(self, request, queryset):
        now = timezone.now()

        if self.value() == "today":
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return queryset.filter(created_at__gte=start_date)
        elif self.value() == "week":
            start_date = now - timedelta(days=7)
            return queryset.filter(created_at__gte=start_date)
        elif self.value() == "month":
            start_date = now - timedelta(days=30)
            return queryset.filter(created_at__gte=start_date)
        elif self.value() == "quarter":
            start_date = now - timedelta(days=90)
            return queryset.filter(created_at__gte=start_date)
        return queryset


@admin.register(Contact)
class ContactAdmin(ExportModelAdmin, ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

    list_display = [
        "id",
        "contact_info",
        "message_preview",
        "message_stats",
        "contact_timing",
        "priority_badge",
    ]
    list_filter = [
        RecentContactFilter,
        MessageLengthFilter,
        ("created_at", RangeDateTimeFilter),
        ("updated_at", RangeDateTimeFilter),
    ]
    search_fields = [
        "name",
        "email",
        "message",
    ]
    readonly_fields = (
        "id",
        "uuid",
        "created_at",
        "updated_at",
        "contact_analytics",
        "message_analytics",
        "timing_info",
    )
    list_per_page = 25
    date_hierarchy = "created_at"

    fieldsets = (
        (
            _("Contact Information"),
            {
                "fields": ("name", "email"),
                "classes": ("wide",),
            },
        ),
        (
            _("Message"),
            {
                "fields": ("message",),
                "classes": ("wide",),
            },
        ),
        (
            _("Analytics"),
            {
                "fields": ("contact_analytics", "message_analytics"),
                "classes": ("collapse",),
            },
        ),
        (
            _("Timing Information"),
            {
                "fields": ("timing_info",),
                "classes": ("collapse",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": ("id", "uuid", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        return super().get_queryset(request)

    def get_ordering(self, request):
        return ["-created_at", "name"]

    def contact_info(self, obj):
        is_suspicious = (
            "@" not in obj.email or "." not in obj.email.split("@")[-1]
        )

        email_class = (
            "text-red-600 dark:text-red-400"
            if is_suspicious
            else "text-base-600 dark:text-base-400"
        )

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="{}">{}</div>'
            '<div class="text-xs text-base-500 dark:text-base-400">ID: {}</div>'
            "</div>",
            obj.name[:30] + ("..." if len(obj.name) > 30 else ""),
            email_class,
            obj.email,
            obj.id,
        )

    contact_info.short_description = _("Contact")

    def message_preview(self, obj):
        message = obj.message
        preview = message[:100] + "..." if len(message) > 100 else message

        preview = preview.replace("\n", " ").replace("\r", " ")

        return format_html(
            '<div class="text-sm">'
            '<div class="text-base-900 dark:text-base-100" title="{}">{}</div>'
            "</div>",
            message.replace('"', "&quot;"),
            preview,
        )

    message_preview.short_description = _("Message")

    def message_stats(self, obj):
        char_count = len(obj.message)
        word_count = len(obj.message.split())
        line_count = obj.message.count("\n") + 1

        if char_count < 100:
            length_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200 rounded-full">'
                "📝 Short"
                "</span>"
            )
        elif char_count < 500:
            length_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-200 rounded-full">'
                "📄 Medium"
                "</span>"
            )
        else:
            length_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200 rounded-full">'
                "📋 Long"
                "</span>"
            )

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{} chars</div>'
            '<div class="text-base-600 dark:text-base-400">{} words</div>'
            '<div class="text-base-500 dark:text-base-400">{} lines</div>'
            '<div class="mt-1">{}</div>'
            "</div>",
            char_count,
            word_count,
            line_count,
            length_badge,
        )

    message_stats.short_description = _("Message Stats")

    def contact_timing(self, obj):
        now = timezone.now()
        diff = now - obj.created_at

        if diff < timedelta(hours=1):
            time_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-200 rounded-full">'
                "🚨 Just Now"
                "</span>"
            )
        elif diff < timedelta(days=1):
            hours_ago = int(diff.total_seconds() // 3600)
            time_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-200 rounded-full">'
                "🕒 {}h ago"
                "</span>",
                hours_ago,
            )
        elif diff < timedelta(days=7):
            time_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-200 rounded-full">'
                "📅 {}d ago"
                "</span>",
                diff.days,
            )
        else:
            time_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-gray-50 dark:bg-gray-800 text-base-700 dark:text-base-200 rounded-full">'
                "📆 Old"
                "</span>"
            )

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="mt-1">{}</div>'
            "</div>",
            obj.created_at.strftime("%Y-%m-%d"),
            time_badge,
        )

    contact_timing.short_description = _("Timing")

    def priority_badge(self, obj):
        now = timezone.now()
        diff = now - obj.created_at
        message_length = len(obj.message)

        if diff < timedelta(hours=2):
            priority = "urgent"
        elif message_length > 500:
            priority = "high"
        elif diff < timedelta(days=1):
            priority = "medium"
        else:
            priority = "low"

        priority_config = {
            "urgent": {
                "bg": "bg-red-50 dark:bg-red-900",
                "text": "text-red-700 dark:text-red-200",
                "icon": "🚨",
                "label": "Urgent",
            },
            "high": {
                "bg": "bg-orange-50 dark:bg-orange-900",
                "text": "text-orange-700 dark:text-orange-200",
                "icon": "⚡",
                "label": "High",
            },
            "medium": {
                "bg": "bg-yellow-50 dark:bg-yellow-900",
                "text": "text-yellow-700 dark:text-yellow-200",
                "icon": "⚠️",
                "label": "Medium",
            },
            "low": {
                "bg": "bg-green-50 dark:bg-green-900",
                "text": "text-green-700 dark:text-green-200",
                "icon": "✅",
                "label": "Low",
            },
        }

        config = priority_config[priority]

        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium {} {} rounded-full gap-1">'
            "<span>{}</span>"
            "<span>{}</span>"
            "</span>",
            config["bg"],
            config["text"],
            config["icon"],
            config["label"],
        )

    priority_badge.short_description = _("Priority")

    def contact_analytics(self, obj):
        now = timezone.now()
        age = now - obj.created_at
        word_count = len(obj.message.split())

        reading_time = max(1, word_count // 200)

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Contact Age:</strong></div><div>{}d {}h</div>"
            "<div><strong>Response Time:</strong></div><div>{}</div>"
            "<div><strong>Email Domain:</strong></div><div>{}</div>"
            "<div><strong>Contact Day:</strong></div><div>{}</div>"
            "<div><strong>Contact Time:</strong></div><div>{}</div>"
            "<div><strong>Reading Time:</strong></div><div>{}min</div>"
            "</div>"
            "</div>",
            age.days,
            age.seconds // 3600,
            "Pending" if age < timedelta(days=1) else "Delayed",
            obj.email.split("@")[-1] if "@" in obj.email else "Invalid",
            obj.created_at.strftime("%A"),
            obj.created_at.strftime("%H:%M"),
            reading_time,
        )

    contact_analytics.short_description = _("Contact Analytics")

    def message_analytics(self, obj):
        message = obj.message
        char_count = len(message)
        word_count = len(message.split())
        sentence_count = (
            message.count(".") + message.count("!") + message.count("?")
        )
        paragraph_count = message.count("\n\n") + 1

        urgent_words = [
            "urgent",
            "emergency",
            "asap",
            "immediately",
            "help",
            "problem",
            "issue",
            "broken",
        ]
        urgency_score = sum(
            1 for word in urgent_words if word.lower() in message.lower()
        )

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Characters:</strong></div><div>{}</div>"
            "<div><strong>Words:</strong></div><div>{}</div>"
            "<div><strong>Sentences:</strong></div><div>{}</div>"
            "<div><strong>Paragraphs:</strong></div><div>{}</div>"
            "<div><strong>Urgency Score:</strong></div><div>{}/10</div>"
            "<div><strong>Avg Word Length:</strong></div><div>{:.1f}</div>"
            "</div>"
            "</div>",
            char_count,
            word_count,
            sentence_count,
            paragraph_count,
            urgency_score,
            char_count / max(word_count, 1),
        )

    message_analytics.short_description = _("Message Analytics")

    def timing_info(self, obj):
        now = timezone.now()
        created_age = now - obj.created_at
        updated_age = now - obj.updated_at

        is_business_hours = 9 <= obj.created_at.hour <= 17
        is_weekend = obj.created_at.weekday() >= 5

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Created:</strong></div><div>{}</div>"
            "<div><strong>Updated:</strong></div><div>{}</div>"
            "<div><strong>Age:</strong></div><div>{}d {}h</div>"
            "<div><strong>Last Modified:</strong></div><div>{}d ago</div>"
            "<div><strong>Business Hours:</strong></div><div>{}</div>"
            "<div><strong>Weekend Contact:</strong></div><div>{}</div>"
            "<div><strong>Time Zone:</strong></div><div>UTC</div>"
            "<div><strong>Season:</strong></div><div>{}</div>"
            "</div>"
            "</div>",
            obj.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            obj.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
            created_age.days,
            created_age.seconds // 3600,
            updated_age.days,
            "Yes" if is_business_hours else "No",
            "Yes" if is_weekend else "No",
            self._get_season(obj.created_at.month),
        )

    timing_info.short_description = _("Timing Information")

    def _get_season(self, month):
        if month in [12, 1, 2]:
            return "Winter"
        elif month in [3, 4, 5]:
            return "Spring"
        elif month in [6, 7, 8]:
            return "Summer"
        else:
            return "Autumn"
