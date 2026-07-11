from datetime import timedelta

from django.contrib import admin
from django.db.models.functions import Length
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import DropdownFilter, RangeDateTimeFilter
from unfold.decorators import display

from admin.displays import format_dt, header_two_line, relative_time
from admin.export import ExportModelAdmin
from contact.models import Contact

CONTACT_PRIORITY_VARIANT: dict[str, str] = {
    "urgent": "danger",
    "high": "warning",
    "medium": "info",
    "low": "success",
}


class MessageLengthFilter(DropdownFilter):
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


class RecentContactFilter(DropdownFilter):
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
class ContactAdmin(ExportModelAdmin):
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
        "priority",
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
            _("System Information"),
            {
                "fields": ("id", "uuid", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_ordering(self, request):
        return ["-created_at", "name"]

    @display(description=_("Contact"), header=True, ordering="name")
    def contact_info(self, obj):
        is_suspicious = (
            not obj.email
            or "@" not in obj.email
            or "." not in obj.email.split("@")[-1]
        )
        email = str(obj.email or _("(no email)"))
        if is_suspicious:
            email = _("%(email)s (invalid)") % {"email": email}
        return header_two_line(obj.name, email)

    @admin.display(description=_("Message"))
    def message_preview(self, obj):
        full = (obj.message or "").replace("\n", " ").replace("\r", " ")
        return full[:100] + ("..." if len(full) > 100 else "")

    @admin.display(description=_("Message Stats"))
    def message_stats(self, obj):
        msg = obj.message or ""
        return _("%(chars)d chars, %(words)d words, %(lines)d lines") % {
            "chars": len(msg),
            "words": len(msg.split()),
            "lines": msg.count("\n") + 1,
        }

    @admin.display(description=_("Timing"), ordering="created_at")
    def contact_timing(self, obj):
        return f"{format_dt(obj.created_at, fmt='%d/%m/%Y')} ({relative_time(obj.created_at)})"

    @display(
        description=_("Priority"),
        label=CONTACT_PRIORITY_VARIANT,
        ordering="created_at",
    )
    def priority(self, obj):
        now = timezone.now()
        diff = now - obj.created_at
        length = len(obj.message or "")

        if diff < timedelta(hours=2):
            return "urgent", _("Urgent")
        if length > 500:
            return "high", _("High")
        if diff < timedelta(days=1):
            return "medium", _("Medium")
        return "low", _("Low")
