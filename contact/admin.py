from datetime import timedelta
import datetime

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

    @admin.display(description=_("Contact"))
    def contact_info(self, obj):
        name = obj.name[:30] + ("..." if len(obj.name) > 30 else "")
        is_suspicious = (
            not obj.email
            or "@" not in obj.email
            or "." not in obj.email.split("@")[-1]
        )
        email_display = obj.email or "(no email)"

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{name}</div>'
            '<div class="{email_class}">{email}</div>'
            '<div class="text-xs text-base-600 dark:text-base-300">ID: {id}</div>'
            "</div>",
            name=name,
            email_class=(
                "text-red-600 dark:text-red-400"
                if is_suspicious
                else "text-base-600 dark:text-base-400"
            ),
            email=email_display,
            id=str(obj.id),
        )

    @admin.display(description=_("Message"))
    def message_preview(self, obj):
        full = obj.message or ""
        preview = full[:100] + ("..." if len(full) > 100 else "")
        preview = preview.replace("\n", " ").replace("\r", " ")
        return format_html(
            '<div class="text-sm">'
            '<div class="text-base-900 dark:text-base-100" title="{title}">{preview}</div>'
            "</div>",
            title=full,
            preview=preview,
        )

    @admin.display(description=_("Message Stats"))
    def message_stats(self, obj):
        msg = obj.message or ""
        char_count = len(msg)
        word_count = len(msg.split())
        line_count = msg.count("\n") + 1

        # Badges
        if char_count < 100:
            length_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200 rounded-full">'
                "📝 Short"
                "</span>"
            )
        elif char_count < 500:
            length_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-200 rounded-full">'
                "📄 Medium"
                "</span>"
            )
        else:
            length_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200 rounded-full">'
                "📋 Long"
                "</span>"
            )

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{chars} chars</div>'
            '<div class="text-base-600 dark:text-base-400">{words} words</div>'
            '<div class="text-base-600 dark:text-base-300">{lines} lines</div>'
            '<div class="mt-1">{badge}</div>'
            "</div>",
            chars=char_count,
            words=word_count,
            lines=line_count,
            badge=length_badge,
        )

    @admin.display(description=_("Timing"))
    def contact_timing(self, obj):
        now = timezone.now()
        diff = now - obj.created_at
        date_str = obj.created_at.strftime("%Y-%m-%d")

        if diff < timedelta(hours=1):
            time_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-200 rounded-full">'
                "🚨 Just Now"
                "</span>"
            )
        elif diff < timedelta(days=1):
            time_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-200 rounded-full">'
                "🕒 {h}h ago"
                "</span>",
                h=int(diff.total_seconds() // 3600),
            )
        elif diff < timedelta(days=7):
            time_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-200 rounded-full">'
                "📅 {d}d ago"
                "</span>",
                d=diff.days,
            )
        else:
            time_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-gray-50 dark:bg-gray-900 text-base-700 dark:text-base-200 rounded-full">'
                "📆 Old"
                "</span>"
            )

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{date}</div>'
            '<div class="mt-1">{badge}</div>'
            "</div>",
            date=date_str,
            badge=time_badge,
        )

    @admin.display(description=_("Priority"))
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

        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            '{bg} {text_class} rounded-full gap-1">'
            "<span>{icon}</span><span>{label}</span>"
            "</span>",
            bg=cfg["bg"],
            text_class=cfg["text"],
            icon=cfg["icon"],
            label=cfg["label"],
        )

    @admin.display(description=_("Contact Analytics"))
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

        return format_html(
            '<div class="text-sm"><div class="grid grid-cols-2 gap-2">'
            "<div><strong>Contact Age:</strong></div><div>{days}d {hours}h</div>"
            "<div><strong>Response Time:</strong></div><div>{status}</div>"
            "<div><strong>Email Domain:</strong></div><div>{domain}</div>"
            "<div><strong>Contact Day:</strong></div><div>{dayname}</div>"
            "<div><strong>Contact Time:</strong></div><div>{time}</div>"
            "<div><strong>Reading Time:</strong></div><div>{reading}min</div>"
            '<div><strong>Priority Score:</strong></div><div class="font-medium">{score}</div>'
            "</div></div>",
            days=age.days,
            hours=age.seconds // 3600,
            status="Pending" if age < timedelta(days=1) else "Delayed",
            domain=obj.email.split("@")[-1] if "@" in obj.email else "Invalid",
            dayname=obj.created_at.strftime("%A"),
            time=obj.created_at.strftime("%H:%M"),
            reading=reading_time,
            score=prio_score,
        )

    @admin.display(description=_("Message Analytics"))
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

        return format_html(
            '<div class="text-sm"><div class="grid grid-cols-2 gap-2">'
            "<div><strong>Characters:</strong></div><div>{chars}</div>"
            "<div><strong>Words:</strong></div><div>{words}</div>"
            "<div><strong>Sentences:</strong></div><div>{sentences}</div>"
            "<div><strong>Paragraphs:</strong></div><div>{paras}</div>"
            "<div><strong>Urgency Score:</strong></div><div>{urgency}/10</div>"
            "<div><strong>Avg Word Length:</strong></div><div>{avg_len}</div>"
            "<div><strong>Reading Time:</strong></div><div>{read_sec} seconds</div>"
            "<div><strong>Sentiment:</strong></div><div>{sentiment}</div>"
            "<div><strong>Complexity:</strong></div><div>{complexity}</div>"
            "<div><strong>Language:</strong></div><div>{language}</div>"
            "</div></div>",
            chars=chars,
            words=words,
            sentences=sentences,
            paras=paras,
            urgency=urgency,
            avg_len=f"{avg_len_val:.1f}",
            read_sec=read_sec,
            sentiment=sentiment,
            complexity=complexity,
            language=language,
        )

    @admin.display(description=_("Timing Information"))
    def timing_info(self, obj):
        now = timezone.now()
        created_age = now - obj.created_at
        updated_age = now - obj.updated_at
        is_business = 9 <= obj.created_at.hour <= 17
        is_weekend = obj.created_at.weekday() >= 5
        season = self._get_season(obj.created_at.month)

        return format_html(
            '<div class="text-sm"><div class="grid grid-cols-2 gap-2">'
            "<div><strong>Created:</strong></div><div>{created_dt}</div>"
            "<div><strong>Updated:</strong></div><div>{updated_dt}</div>"
            "<div><strong>Age:</strong></div><div>{cdays}d {chours}h</div>"
            "<div><strong>Last Modified:</strong></div><div>{udays}d ago</div>"
            "<div><strong>Business Hours:</strong></div><div>{bhours}</div>"
            "<div><strong>Weekend Contact:</strong></div><div>{weekend}</div>"
            "<div><strong>Season:</strong></div><div>{season}</div>"
            "</div></div>",
            created_dt=obj.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            updated_dt=obj.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
            cdays=created_age.days,
            chours=created_age.seconds // 3600,
            udays=updated_age.days,
            bhours="Yes" if is_business else "No",
            weekend="Yes" if is_weekend else "No",
            season=season,
        )

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
