from datetime import timedelta
import datetime

from django.contrib import admin
from django.db.models.functions import Length
from django.utils import timezone
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe
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
            return queryset.filter(created_at__gte=now - timedelta(days=7))
        elif self.value() == "month":
            return queryset.filter(created_at__gte=now - timedelta(days=30))
        elif self.value() == "quarter":
            return queryset.filter(created_at__gte=now - timedelta(days=90))
        return queryset


@admin.register(Contact)
class ContactAdmin(ExportModelAdmin, ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

    list_display = [
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
    search_fields = ["name", "email", "message"]
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
        name = obj.name[:30] + ("..." if len(obj.name) > 30 else "")
        is_suspicious = (
            not obj.email
            or "@" not in obj.email
            or "." not in obj.email.split("@")[-1]
        )
        email_display = obj.email or "(no email)"

        safe_name = conditional_escape(name)
        safe_email = conditional_escape(email_display)
        safe_email_class = conditional_escape(
            "text-red-600 dark:text-red-400"
            if is_suspicious
            else "text-base-600 dark:text-base-400"
        )
        safe_id = conditional_escape(str(obj.id))

        html = (
            f'<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{safe_name}</div>'
            f'<div class="{safe_email_class}">{safe_email}</div>'
            f'<div class="text-xs text-base-500 dark:text-base-400">ID: {safe_id}</div>'
            f"</div>"
        )
        return mark_safe(html)

    contact_info.short_description = _("Contact")

    def message_preview(self, obj):
        full = obj.message or ""
        preview = full[:100] + ("..." if len(full) > 100 else "")
        preview = preview.replace("\n", " ").replace("\r", " ")
        title_attr = full.replace('"', "&quot;")

        safe_title = conditional_escape(title_attr)
        safe_preview = conditional_escape(preview)

        html = (
            f'<div class="text-sm">'
            f'<div class="text-base-900 dark:text-base-100" title="{safe_title}">{safe_preview}</div>'
            f"</div>"
        )
        return mark_safe(html)

    message_preview.short_description = _("Message")

    def message_stats(self, obj):
        msg = obj.message or ""
        char_count = len(msg)
        word_count = len(msg.split())
        line_count = msg.count("\n") + 1

        # Badges
        if char_count < 100:
            length_badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200 rounded-full">'
                "📝 Short"
                "</span>"
            )
        elif char_count < 500:
            length_badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-200 rounded-full">'
                "📄 Medium"
                "</span>"
            )
        else:
            length_badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200 rounded-full">'
                "📋 Long"
                "</span>"
            )

        safe_chars = conditional_escape(str(char_count))
        safe_words = conditional_escape(str(word_count))
        safe_lines = conditional_escape(str(line_count))

        html = (
            f'<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{safe_chars} chars</div>'
            f'<div class="text-base-600 dark:text-base-400">{safe_words} words</div>'
            f'<div class="text-base-500 dark:text-base-400">{safe_lines} lines</div>'
            f'<div class="mt-1">{length_badge}</div>'
            f"</div>"
        )
        return mark_safe(html)

    message_stats.short_description = _("Message Stats")

    def contact_timing(self, obj):
        now = timezone.now()
        diff = now - obj.created_at
        date_str = obj.created_at.strftime("%Y-%m-%d")

        if diff < timedelta(hours=1):
            time_badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-200 rounded-full">'
                "🚨 Just Now"
                "</span>"
            )
        elif diff < timedelta(days=1):
            hours_ago = str(int(diff.total_seconds() // 3600))
            safe_hours = conditional_escape(hours_ago)
            time_badge = mark_safe(
                f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                f'bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-200 rounded-full">'
                f"🕒 {safe_hours}h ago"
                f"</span>"
            )
        elif diff < timedelta(days=7):
            days_ago = str(diff.days)
            safe_days = conditional_escape(days_ago)
            time_badge = mark_safe(
                f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                f'bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-200 rounded-full">'
                f"📅 {safe_days}d ago"
                f"</span>"
            )
        else:
            time_badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-gray-50 dark:bg-gray-900 text-base-700 dark:text-base-200 rounded-full">'
                "📆 Old"
                "</span>"
            )

        safe_date = conditional_escape(date_str)
        html = (
            f'<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{safe_date}</div>'
            f'<div class="mt-1">{time_badge}</div>'
            f"</div>"
        )
        return mark_safe(html)

    contact_timing.short_description = _("Timing")

    def priority_badge(self, obj):
        now = timezone.now()
        diff = now - obj.created_at
        length = len(obj.message or "")

        if diff < timedelta(hours=2):
            key = "urgent"
        elif length > 500:
            key = "high"
        elif diff < timedelta(days=1):
            key = "medium"
        else:
            key = "low"

        cfg = {
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
        }[key]

        safe_bg = conditional_escape(cfg["bg"])
        safe_text = conditional_escape(cfg["text"])
        safe_icon = conditional_escape(cfg["icon"])
        safe_label = conditional_escape(cfg["label"])

        html = (
            f'<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            f'{safe_bg} {safe_text} rounded-full gap-1">'
            f"<span>{safe_icon}</span><span>{safe_label}</span>"
            f"</span>"
        )
        return mark_safe(html)

    priority_badge.short_description = _("Priority")

    def contact_analytics(self, obj):
        if not obj.created_at:
            return "Available after creation."

        now = timezone.now()
        age = now - obj.created_at
        words = len((obj.message or "").split())
        score_map = {"low": 25, "medium": 50, "high": 75, "urgent": 100}

        # reuse priority logic
        if age < timedelta(hours=2):
            prio = "urgent"
        elif len(obj.message or "") > 500:
            prio = "high"
        elif age < timedelta(days=1):
            prio = "medium"
        else:
            prio = "low"
        prio_score = score_map[prio]
        reading_time = max(1, words // 200)

        safe_days = conditional_escape(str(age.days))
        safe_hours = conditional_escape(str(age.seconds // 3600))
        safe_status = conditional_escape(
            "Pending" if age < timedelta(days=1) else "Delayed"
        )
        safe_domain = conditional_escape(
            obj.email.split("@")[-1] if "@" in obj.email else "Invalid"
        )
        safe_dayname = conditional_escape(obj.created_at.strftime("%A"))
        safe_time = conditional_escape(obj.created_at.strftime("%H:%M"))
        safe_reading = conditional_escape(str(reading_time))
        safe_score = conditional_escape(str(prio_score))

        html = (
            f'<div class="text-sm"><div class="grid grid-cols-2 gap-2">'
            f"<div><strong>Contact Age:</strong></div><div>{safe_days}d {safe_hours}h</div>"
            f"<div><strong>Response Time:</strong></div><div>{safe_status}</div>"
            f"<div><strong>Email Domain:</strong></div><div>{safe_domain}</div>"
            f"<div><strong>Contact Day:</strong></div><div>{safe_dayname}</div>"
            f"<div><strong>Contact Time:</strong></div><div>{safe_time}</div>"
            f"<div><strong>Reading Time:</strong></div><div>{safe_reading}min</div>"
            f'<div><strong>Priority Score:</strong></div><div class="font-medium">{safe_score}</div>'
            f"</div></div>"
        )
        return mark_safe(html)

    contact_analytics.short_description = _("Contact Analytics")

    def message_analytics(self, obj):
        msg = obj.message or ""
        chars = len(msg)
        words = len(msg.split())
        sentences = msg.count(".") + msg.count("!") + msg.count("?")
        paras = msg.count("\n\n") + 1
        urgent_terms = [
            "urgent",
            "emergency",
            "asap",
            "immediately",
            "help",
            "problem",
            "issue",
            "broken",
        ]
        urgency = sum(1 for w in urgent_terms if w in msg.lower())
        read_sec = 0 if words == 0 else max(1, int((words / 200) * 60))
        sentiment = "Urgent" if urgency >= 3 else "Neutral"
        avg_len_val = chars / max(words, 1)
        complexity = "Complex" if avg_len_val > 5 else "Simple"
        language = "English"

        safe_chars = conditional_escape(str(chars))
        safe_words = conditional_escape(str(words))
        safe_sentences = conditional_escape(str(sentences))
        safe_paras = conditional_escape(str(paras))
        safe_urgency = conditional_escape(str(urgency))
        safe_avg_len = conditional_escape(f"{avg_len_val:.1f}")
        safe_read_sec = conditional_escape(str(read_sec))
        safe_sentiment = conditional_escape(sentiment)
        safe_complexity = conditional_escape(complexity)
        safe_language = conditional_escape(language)

        html = (
            f'<div class="text-sm"><div class="grid grid-cols-2 gap-2">'
            f"<div><strong>Characters:</strong></div><div>{safe_chars}</div>"
            f"<div><strong>Words:</strong></div><div>{safe_words}</div>"
            f"<div><strong>Sentences:</strong></div><div>{safe_sentences}</div>"
            f"<div><strong>Paragraphs:</strong></div><div>{safe_paras}</div>"
            f"<div><strong>Urgency Score:</strong></div><div>{safe_urgency}/10</div>"
            f"<div><strong>Avg Word Length:</strong></div><div>{safe_avg_len}</div>"
            f"<div><strong>Reading Time:</strong></div><div>{safe_read_sec} seconds</div>"
            f"<div><strong>Sentiment:</strong></div><div>{safe_sentiment}</div>"
            f"<div><strong>Complexity:</strong></div><div>{safe_complexity}</div>"
            f"<div><strong>Language:</strong></div><div>{safe_language}</div>"
            f"</div></div>"
        )
        return mark_safe(html)

    message_analytics.short_description = _("Message Analytics")

    def timing_info(self, obj):
        now = timezone.now()
        created_age = now - obj.created_at
        updated_age = now - obj.updated_at
        is_business = 9 <= obj.created_at.hour <= 17
        is_weekend = obj.created_at.weekday() >= 5
        season = self._get_season(obj.created_at.month)

        safe_created_dt = conditional_escape(
            obj.created_at.strftime("%Y-%m-%d %H:%M:%S")
        )
        safe_updated_dt = conditional_escape(
            obj.updated_at.strftime("%Y-%m-%d %H:%M:%S")
        )
        safe_cdays = conditional_escape(str(created_age.days))
        safe_chours = conditional_escape(str(created_age.seconds // 3600))
        safe_udays = conditional_escape(str(updated_age.days))
        safe_bhours = conditional_escape("Yes" if is_business else "No")
        safe_weekend = conditional_escape("Yes" if is_weekend else "No")
        safe_season = conditional_escape(season)

        html = (
            f'<div class="text-sm"><div class="grid grid-cols-2 gap-2">'
            f"<div><strong>Created:</strong></div><div>{safe_created_dt}</div>"
            f"<div><strong>Updated:</strong></div><div>{safe_updated_dt}</div>"
            f"<div><strong>Age:</strong></div><div>{safe_cdays}d {safe_chours}h</div>"
            f"<div><strong>Last Modified:</strong></div><div>{safe_udays}d ago</div>"
            f"<div><strong>Business Hours:</strong></div><div>{safe_bhours}</div>"
            f"<div><strong>Weekend Contact:</strong></div><div>{safe_weekend}</div>"
            f"<div><strong>Season:</strong></div><div>{safe_season}</div>"
            f"</div></div>"
        )
        return mark_safe(html)

    timing_info.short_description = _("Timing Information")

    def _get_season(self, month_or_date):
        if isinstance(month_or_date, (datetime.date, datetime.datetime)):
            m = month_or_date.month
        else:
            m = int(month_or_date)
        if m in (12, 1, 2):
            return "Winter"
        if m in (3, 4, 5):
            return "Spring"
        if m in (6, 7, 8):
            return "Summer"
        return "Autumn"
