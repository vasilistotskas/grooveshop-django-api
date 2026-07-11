from datetime import timedelta

from django.contrib import admin
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import DropdownFilter, RangeDateTimeFilter
from unfold.decorators import display

from admin.base import BaseModelAdmin, BaseTranslatableAdmin
from admin.displays import (
    choice_label,
    format_dt,
    header_two_line,
    relative_time,
)
from notification.enum import (
    NotificationCategoryEnum,
    NotificationKindEnum,
    NotificationPriorityEnum,
)
from notification.models.notification import Notification
from notification.models.user import NotificationUser

# ── Local (single-app) TextChoices variant maps ────────────────────────
# Notification kind/category/priority only exist in this app, so their
# colour maps live here rather than in the shared ``admin.displays``
# vocabulary.

NOTIFICATION_KIND_VARIANT: dict[str, str] = {
    NotificationKindEnum.INFO: "info",
    NotificationKindEnum.SUCCESS: "success",
    NotificationKindEnum.WARNING: "warning",
    NotificationKindEnum.ERROR: "danger",
    NotificationKindEnum.DANGER: "danger",
}

NOTIFICATION_PRIORITY_VARIANT: dict[str, str] = {
    NotificationPriorityEnum.LOW: "default",
    NotificationPriorityEnum.NORMAL: "info",
    NotificationPriorityEnum.HIGH: "warning",
    NotificationPriorityEnum.URGENT: "danger",
    NotificationPriorityEnum.CRITICAL: "danger",
}

NOTIFICATION_CATEGORY_VARIANT: dict[str, str] = {
    NotificationCategoryEnum.ORDER: "info",
    NotificationCategoryEnum.PAYMENT: "primary",
    NotificationCategoryEnum.SHIPPING: "info",
    NotificationCategoryEnum.CART: "warning",
    NotificationCategoryEnum.PRODUCT: "default",
    NotificationCategoryEnum.ACCOUNT: "default",
    NotificationCategoryEnum.SECURITY: "danger",
    NotificationCategoryEnum.PROMOTION: "success",
    NotificationCategoryEnum.SYSTEM: "default",
    NotificationCategoryEnum.REVIEW: "warning",
    NotificationCategoryEnum.WISHLIST: "default",
    NotificationCategoryEnum.SUPPORT: "info",
    NotificationCategoryEnum.NEWSLETTER: "default",
    NotificationCategoryEnum.RECOMMENDATION: "default",
}


class NotificationStatusFilter(DropdownFilter):
    title = _("Notification Status")
    parameter_name = "notification_status"

    def lookups(self, request, model_admin):
        return [
            ("active", _("Active")),
            ("expired", _("Expired")),
            ("urgent", _("Urgent Priority")),
            ("recent", _("Recent (Last 7 days)")),
            ("with_link", _("Has Link")),
            ("system", _("System Notifications")),
            ("order", _("Order Related")),
            ("payment", _("Payment Related")),
            ("security", _("Security Related")),
            ("promotion", _("Promotions")),
        ]

    def queryset(self, request, queryset):
        filter_value = self.value()
        now = timezone.now()

        match filter_value:
            case "active":
                return queryset.filter(
                    models.Q(expiry_date__isnull=True)
                    | models.Q(expiry_date__gt=now)
                )
            case "expired":
                filter_kwargs = {"expiry_date__lt": now}
            case "urgent":
                filter_kwargs = {"priority__in": ["URGENT", "CRITICAL"]}
            case "recent":
                week_ago = now - timedelta(days=7)
                filter_kwargs = {"created_at__gte": week_ago}
            case "with_link":
                return queryset.exclude(link="")
            case "system":
                filter_kwargs = {"category": "SYSTEM"}
            case "order":
                filter_kwargs = {"category": "ORDER"}
            case "payment":
                filter_kwargs = {"category": "PAYMENT"}
            case "security":
                filter_kwargs = {"category": "SECURITY"}
            case "promotion":
                filter_kwargs = {"category": "PROMOTION"}
            case _:
                return queryset

        return queryset.filter(**filter_kwargs)


class NotificationUserStatusFilter(DropdownFilter):
    title = _("User Status")
    parameter_name = "user_status"

    def lookups(self, request, model_admin):
        return [
            ("seen", _("Seen")),
            ("unseen", _("Unseen")),
            ("recent_seen", _("Seen Today")),
            ("urgent_unseen", _("Urgent & Unseen")),
            ("expired", _("Expired Notifications")),
        ]

    def queryset(self, request, queryset):
        filter_value = self.value()
        now = timezone.now()
        today = now.date()

        match filter_value:
            case "seen":
                filter_kwargs = {"seen": True}
            case "unseen":
                filter_kwargs = {"seen": False}
            case "recent_seen":
                filter_kwargs = {"seen": True, "seen_at__date": today}
            case "urgent_unseen":
                filter_kwargs = {
                    "seen": False,
                    "notification__priority__in": ["URGENT", "CRITICAL"],
                }
            case "expired":
                filter_kwargs = {"notification__expiry_date__lt": now}
            case _:
                return queryset

        return queryset.filter(**filter_kwargs)


@admin.register(Notification)
class NotificationAdmin(BaseTranslatableAdmin):
    list_display = [
        "notification_info",
        "kind_label",
        "category_label",
        "priority_label",
        "expiry_status",
        "engagement_stats",
        "timing_info",
    ]
    list_filter = [
        NotificationStatusFilter,
        "kind",
        "category",
        "priority",
        ("created_at", RangeDateTimeFilter),
        ("expiry_date", RangeDateTimeFilter),
    ]
    search_fields = [
        "translations__title",
        "translations__message",
        "notification_type",
        "link",
    ]
    readonly_fields = (
        "uuid",
        "created_at",
        "updated_at",
        "notification_analytics",
        "engagement_summary",
        "timing_summary",
    )
    date_hierarchy = "created_at"

    fieldsets = (
        (
            _("Notification Content"),
            {
                "fields": ("title", "message", "link"),
                "classes": ("wide",),
            },
        ),
        (
            _("Classification"),
            {
                "fields": ("kind", "category", "priority", "notification_type"),
                "classes": ("wide",),
            },
        ),
        (
            _("Scheduling"),
            {
                "fields": ("expiry_date",),
                "classes": ("wide",),
            },
        ),
        (
            _("Analytics"),
            {
                "fields": ("notification_analytics", "engagement_summary"),
                "classes": ("collapse",),
            },
        ),
        (
            _("Timing Information"),
            {
                "fields": ("timing_summary",),
                "classes": ("collapse",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": ("uuid", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        # ``Notification`` is a broadcast model — it has no ``user``
        # field (per-user delivery lives on ``UserNotification`` via
        # the ``notification_users`` reverse manager). The previous
        # ``prefetch_related("user__user")`` was always invalid and
        # raised a 500 on every changelist load with non-empty data.
        qs = super().get_queryset(request).prefetch_related("translations")
        return qs.annotate(
            nu_total=Count("notification_users"),
            nu_seen=Count(
                "notification_users",
                filter=Q(notification_users__seen=True),
            ),
        )

    @staticmethod
    def _engagement_counts(obj):
        total = getattr(obj, "nu_total", None)
        if total is None:
            total = obj.notification_users.count()
        seen = getattr(obj, "nu_seen", None)
        if seen is None:
            seen = obj.notification_users.filter(seen=True).count()
        rate = (seen / total * 100) if total else 0
        return total, seen, total - seen, rate

    @display(description=_("Notification"), ordering="created_at")
    def notification_info(self, obj):
        title = obj.safe_translation_getter("title", any_language=True) or _(
            "No title"
        )
        message = obj.safe_translation_getter(
            "message", any_language=True
        ) or _("No message")
        title_display = title[:40] + "…" if len(title) > 40 else title
        message_preview = message[:60] + "…" if len(message) > 60 else message
        return f"{title_display} — {message_preview}"

    kind_label = choice_label(
        "kind", variants=NOTIFICATION_KIND_VARIANT, description=_("Kind")
    )
    category_label = choice_label(
        "category",
        variants=NOTIFICATION_CATEGORY_VARIANT,
        description=_("Category"),
    )
    priority_label = choice_label(
        "priority",
        variants=NOTIFICATION_PRIORITY_VARIANT,
        description=_("Priority"),
    )

    @display(
        description=_("Status"),
        label={"expired": "danger", "active": "success"},
    )
    def expiry_status(self, obj):
        if obj.is_expired():
            return "expired", _("Expired")
        return "active", _("Active")

    @display(description=_("Engagement"))
    def engagement_stats(self, obj):
        total, seen, _unseen, rate = self._engagement_counts(obj)
        return _("%(seen)d/%(total)d seen (%(rate).0f%%)") % {
            "seen": seen,
            "total": total,
            "rate": rate,
        }

    @display(description=_("Timing"), ordering="created_at")
    def timing_info(self, obj):
        if obj.expiry_date:
            expiry = (
                _("expired")
                if obj.is_expired()
                else format_dt(obj.expiry_date, fmt="%d/%m")
            )
        else:
            expiry = _("no expiry")
        return (
            f"{format_dt(obj.created_at, fmt='%d/%m')} "
            f"({relative_time(obj.created_at)}) · {expiry}"
        )

    @display(description=_("Analytics"))
    def notification_analytics(self, obj):
        if not obj.pk:
            return _("Available after creation.")
        title = obj.safe_translation_getter("title", any_language=True) or ""
        message = (
            obj.safe_translation_getter("message", any_language=True) or ""
        )
        return _(
            "Title %(title_len)d chars · Message %(msg_len)d chars · "
            "Link: %(link)s"
        ) % {
            "title_len": len(title),
            "msg_len": len(message),
            "link": _("Yes") if obj.link else _("No"),
        }

    @display(description=_("Engagement Summary"))
    def engagement_summary(self, obj):
        if not obj.pk:
            return _("Available after creation.")
        total, seen, unseen, rate = self._engagement_counts(obj)
        return _(
            "%(seen)d seen, %(unseen)d unseen of %(total)d recipients "
            "(%(rate).1f%%)"
        ) % {"seen": seen, "unseen": unseen, "total": total, "rate": rate}

    @display(description=_("Timing Summary"))
    def timing_summary(self, obj):
        if not obj.created_at:
            return _("Available after creation.")
        expiry = (
            format_dt(obj.expiry_date, fmt="%d/%m/%Y")
            if obj.expiry_date
            else _("Never")
        )
        status = _("Expired") if obj.is_expired() else _("Active")
        return _(
            "Created %(created)s (%(age)s) · Expires %(expiry)s · %(status)s"
        ) % {
            "created": format_dt(obj.created_at),
            "age": relative_time(obj.created_at),
            "expiry": expiry,
            "status": status,
        }


@admin.register(NotificationUser)
class NotificationUserAdmin(BaseModelAdmin):
    list_display = [
        "user_info",
        "notification_info",
        "seen_label",
        "priority_label",
        "timing_display",
    ]
    list_filter = [
        NotificationUserStatusFilter,
        "seen",
        ("created_at", RangeDateTimeFilter),
        ("seen_at", RangeDateTimeFilter),
        "notification__priority",
        "notification__category",
    ]
    search_fields = [
        "user__email",
        "user__username",
        "notification__translations__title",
    ]
    readonly_fields = (
        "uuid",
        "created_at",
        "updated_at",
        "user_notification_analytics",
    )
    list_select_related = ["user", "notification"]
    list_per_page = 50
    date_hierarchy = "created_at"

    fieldsets = (
        (
            _("Relationship"),
            {
                "fields": ("user", "notification"),
                "classes": ("wide",),
            },
        ),
        (
            _("Status"),
            {
                "fields": ("seen", "seen_at"),
                "classes": ("wide",),
            },
        ),
        (
            _("Analytics"),
            {
                "fields": ("user_notification_analytics",),
                "classes": ("collapse",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": ("uuid", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("user", "notification")
            .prefetch_related("notification__translations")
        )

    @display(description=_("User"), header=True, ordering="user__email")
    def user_info(self, obj):
        return header_two_line(
            obj.user.full_name or obj.user.username, obj.user.email
        )

    @display(description=_("Notification"), ordering="notification__created_at")
    def notification_info(self, obj):
        notification = obj.notification
        title = notification.safe_translation_getter(
            "title", any_language=True
        ) or _("No title")
        title_display = title[:30] + "…" if len(title) > 30 else title
        return f"{title_display} ({notification.get_kind_display()})"

    @display(
        description=_("Seen"),
        label={"seen": "success", "unseen": "warning"},
        ordering="seen",
    )
    def seen_label(self, obj):
        return ("seen", _("Seen")) if obj.seen else ("unseen", _("Unseen"))

    @display(
        description=_("Priority"),
        label=NOTIFICATION_PRIORITY_VARIANT,
        ordering="notification__priority",
    )
    def priority_label(self, obj):
        return (
            obj.notification.priority,
            obj.notification.get_priority_display(),
        )

    @display(description=_("Timing"), ordering="created_at")
    def timing_display(self, obj):
        status = _("Expired") if obj.notification.is_expired() else _("Active")
        return (
            f"{format_dt(obj.created_at, fmt='%d/%m')} "
            f"({relative_time(obj.created_at)}) · {status}"
        )

    @display(description=_("Analytics"))
    def user_notification_analytics(self, obj):
        if not obj.pk:
            return _("Available after creation.")
        response = (
            relative_time(obj.created_at, now=obj.seen_at)
            if obj.seen and obj.seen_at
            else _("N/A")
        )
        status = _("Expired") if obj.notification.is_expired() else _("Active")
        return _(
            "Response time %(response)s · Notification %(status)s · "
            "%(priority)s / %(category)s"
        ) % {
            "response": response,
            "status": status,
            "priority": obj.notification.get_priority_display(),
            "category": obj.notification.get_category_display(),
        }
