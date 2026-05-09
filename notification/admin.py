from datetime import timedelta

from django.contrib import admin
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from parler.admin import TranslatableAdmin
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateTimeFilter,
)

from notification.models.notification import Notification
from notification.models.user import NotificationUser


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
class NotificationAdmin(ModelAdmin, TranslatableAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

    list_display = [
        "notification_info",
        "priority_badge",
        "category_badge",
        "status_display",
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
    list_select_related = []
    list_per_page = 25
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
        qs = super().get_queryset(request).prefetch_related("user__user")
        return qs.annotate(
            nu_total=Count("notification_users"),
            nu_seen=Count(
                "notification_users",
                filter=Q(notification_users__seen=True),
            ),
        )

    @admin.display(description=_("Notification"))
    def notification_info(self, obj):
        title = (
            obj.safe_translation_getter("title", any_language=True)
            or "No Title"
        )
        message = (
            obj.safe_translation_getter("message", any_language=True)
            or "No Message"
        )

        title_display = title[:40] + "..." if len(title) > 40 else title
        message_preview = message[:60] + "..." if len(message) > 60 else message

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100 flex items-center gap-2">'
            "<span>{title}</span>"
            '<span class="text-blue-500">{link_icon}</span>'
            "</div>"
            '<div class="text-base-600 dark:text-base-400">{message}</div>'
            '<div class="text-xs text-base-600 dark:text-base-300">ID: {id}</div>'
            "</div>",
            title=title_display,
            link_icon="🔗" if obj.link else "",
            message=message_preview,
            id=obj.id,
        )

    @admin.display(description=_("Priority"))
    def priority_badge(self, obj):
        priority_config = {
            "LOW": {
                "bg": "bg-gray-50 dark:bg-gray-900",
                "text": "text-base-700 dark:text-base-700",
                "icon": "🔽",
            },
            "NORMAL": {
                "bg": "bg-blue-50 dark:bg-blue-900",
                "text": "text-blue-700 dark:text-blue-200",
                "icon": "ℹ️",  # noqa: RUF001
            },
            "HIGH": {
                "bg": "bg-orange-50 dark:bg-orange-900",
                "text": "text-orange-700 dark:text-orange-200",
                "icon": "⚠️",
            },
            "URGENT": {
                "bg": "bg-red-50 dark:bg-red-900",
                "text": "text-red-700 dark:text-red-200",
                "icon": "🚨",
            },
            "CRITICAL": {
                "bg": "bg-purple-50 dark:bg-purple-900",
                "text": "text-purple-700 dark:text-purple-200",
                "icon": "🔴",
            },
        }

        config = priority_config.get(obj.priority, priority_config["NORMAL"])
        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            '{bg} {text_class} rounded-full gap-1">'
            "<span>{icon}</span>"
            "<span>{priority}</span>"
            "</span>",
            bg=config["bg"],
            text_class=config["text"],
            icon=config["icon"],
            priority=obj.get_priority_display(),
        )

    @admin.display(description=_("Category"))
    def category_badge(self, obj):
        category_config = {
            "ORDER": {
                "bg": "bg-green-50 dark:bg-green-900",
                "text": "text-green-700 dark:text-green-200",
                "icon": "📦",
            },
            "PAYMENT": {
                "bg": "bg-blue-50 dark:bg-blue-900",
                "text": "text-blue-700 dark:text-blue-200",
                "icon": "💳",
            },
            "SHIPPING": {
                "bg": "bg-purple-50 dark:bg-purple-900",
                "text": "text-purple-700 dark:text-purple-200",
                "icon": "🚚",
            },
            "CART": {
                "bg": "bg-yellow-50 dark:bg-yellow-900",
                "text": "text-yellow-700 dark:text-yellow-200",
                "icon": "🛒",
            },
            "PRODUCT": {
                "bg": "bg-indigo-50 dark:bg-indigo-900",
                "text": "text-indigo-700 dark:text-indigo-200",
                "icon": "🏷️",
            },
            "ACCOUNT": {
                "bg": "bg-cyan-50 dark:bg-cyan-900",
                "text": "text-cyan-700 dark:text-cyan-200",
                "icon": "👤",
            },
            "SECURITY": {
                "bg": "bg-red-50 dark:bg-red-900",
                "text": "text-red-700 dark:text-red-200",
                "icon": "🔒",
            },
            "PROMOTION": {
                "bg": "bg-pink-50 dark:bg-pink-900",
                "text": "text-pink-700 dark:text-pink-200",
                "icon": "🎉",
            },
            "SYSTEM": {
                "bg": "bg-gray-50 dark:bg-gray-900",
                "text": "text-base-700 dark:text-base-700",
                "icon": "⚙️",
            },
            "REVIEW": {
                "bg": "bg-orange-50 dark:bg-orange-900",
                "text": "text-orange-700 dark:text-orange-200",
                "icon": "⭐",
            },
            "WISHLIST": {
                "bg": "bg-rose-50 dark:bg-rose-900",
                "text": "text-rose-700 dark:text-rose-200",
                "icon": "❤️",
            },
            "SUPPORT": {
                "bg": "bg-teal-50 dark:bg-teal-900",
                "text": "text-teal-700 dark:text-teal-200",
                "icon": "🎧",
            },
            "NEWSLETTER": {
                "bg": "bg-violet-50 dark:bg-violet-900",
                "text": "text-violet-700 dark:text-violet-200",
                "icon": "📧",
            },
            "RECOMMENDATION": {
                "bg": "bg-emerald-50 dark:bg-emerald-900",
                "text": "text-emerald-700 dark:text-emerald-200",
                "icon": "💡",
            },
        }

        config = category_config.get(obj.category, category_config["SYSTEM"])
        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            '{bg} {text_class} rounded border gap-1">'
            "<span>{icon}</span>"
            "<span>{category}</span>"
            "</span>",
            bg=config["bg"],
            text_class=config["text"],
            icon=config["icon"],
            category=obj.get_category_display(),
        )

    @admin.display(description=_("Status"))
    def status_display(self, obj):
        now = timezone.now()
        is_expired = obj.expiry_date and now > obj.expiry_date

        if is_expired:
            status_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-200 rounded-full">'
                "❌ Expired"
                "</span>"
            )
        else:
            status_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200 rounded-full">'
                "✅ Active"
                "</span>"
            )

        kind_config = {
            "INFO": {"color": "text-blue-600 dark:text-blue-400", "icon": "ℹ️"},  # noqa: RUF001
            "SUCCESS": {
                "color": "text-green-600 dark:text-green-400",
                "icon": "✅",
            },
            "WARNING": {
                "color": "text-yellow-600 dark:text-yellow-400",
                "icon": "⚠️",
            },
            "ERROR": {"color": "text-red-600 dark:text-red-400", "icon": "❌"},
            "DANGER": {"color": "text-red-700 dark:text-red-300", "icon": "🚨"},
        }

        kind_display = kind_config.get(obj.kind, kind_config["INFO"])
        return format_html(
            '<div class="text-sm space-y-1">'
            "<div>{status_badge}</div>"
            '<div class="flex items-center gap-1">'
            '<span class="{kind_color}">{kind_icon}</span>'
            '<span class="text-base-600 dark:text-base-400">{kind_label}</span>'
            "</div>"
            "</div>",
            status_badge=status_badge,
            kind_color=kind_display["color"],
            kind_icon=kind_display["icon"],
            kind_label=obj.get_kind_display(),
        )

    @admin.display(description=_("Engagement"))
    def engagement_stats(self, obj):
        total_users = getattr(obj, "nu_total", obj.notification_users.count())
        seen_users = getattr(
            obj, "nu_seen", obj.notification_users.filter(seen=True).count()
        )
        unseen_users = total_users - seen_users

        engagement_rate = (seen_users / max(total_users, 1)) * 100

        if engagement_rate >= 75:
            rate_color = "text-green-600 dark:text-green-400"
            rate_icon = "📈"
        elif engagement_rate >= 50:
            rate_color = "text-yellow-600 dark:text-yellow-400"
            rate_icon = "📊"
        else:
            rate_color = "text-red-600 dark:text-red-400"
            rate_icon = "📉"

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">👥 {total}</div>'
            '<div class="text-green-600 dark:text-green-400">👁️ {seen}</div>'
            '<div class="text-base-600 dark:text-base-400">👓 {unseen}</div>'
            '<div class="flex items-center gap-1 {rate_color}"><span>{rate_icon}</span><span>{rate}</span></div>'
            "</div>",
            total=total_users,
            seen=seen_users,
            unseen=unseen_users,
            rate_color=rate_color,
            rate_icon=rate_icon,
            rate=f"{engagement_rate:.1f}%",
        )

    @admin.display(description=_("Timing"))
    def timing_info(self, obj):
        if not obj.created_at:
            return "Available after creation."

        now = timezone.now()
        age = now - obj.created_at

        if obj.expiry_date:
            if now > obj.expiry_date:
                expiry_status = format_html(
                    '<span class="text-red-600 dark:text-red-400">Expired</span>'
                )
            else:
                time_left = obj.expiry_date - now
                if time_left.days > 0:
                    expiry_status = format_html(
                        '<span class="text-green-600 dark:text-green-400">{d}d left</span>',
                        d=time_left.days,
                    )
                else:
                    expiry_status = format_html(
                        '<span class="text-orange-600 dark:text-orange-400">{h}h left</span>',
                        h=time_left.seconds // 3600,
                    )
        else:
            expiry_status = format_html(
                '<span class="text-blue-600 dark:text-blue-400">No expiry</span>'
            )

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{date}</div>'
            '<div class="text-base-600 dark:text-base-400">{age}d ago</div>'
            "<div>{expiry}</div>"
            "</div>",
            date=obj.created_at.strftime("%m-%d"),
            age=age.days,
            expiry=expiry_status,
        )

    @admin.display(description=_("Analytics"))
    def notification_analytics(self, obj):
        if not obj.created_at:
            return "Available after creation."

        now = timezone.now()
        age = now - obj.created_at

        title_length = len(
            obj.safe_translation_getter("title", any_language=True) or ""
        )
        message_length = len(
            obj.safe_translation_getter("message", any_language=True) or ""
        )

        has_link = bool(obj.link)
        has_type = bool(obj.notification_type)

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Age:</strong></div><div>{days}d {hours}h</div>"
            "<div><strong>Title Length:</strong></div><div>{title_len} chars</div>"
            "<div><strong>Message Length:</strong></div><div>{msg_len} chars</div>"
            "<div><strong>Has Link:</strong></div><div>{link}</div>"
            "<div><strong>Has Type:</strong></div><div>{type}</div>"
            "<div><strong>Readability:</strong></div><div>{readability}</div>"
            "</div>"
            "</div>",
            days=age.days,
            hours=age.seconds // 3600,
            title_len=title_length,
            msg_len=message_length,
            link="Yes" if has_link else "No",
            type="Yes" if has_type else "No",
            readability=(
                "Good"
                if 10 <= title_length <= 50 and 20 <= message_length <= 200
                else "Review"
            ),
        )

    @admin.display(description=_("Engagement Summary"))
    def engagement_summary(self, obj):
        total_users = getattr(obj, "nu_total", obj.notification_users.count())
        seen_users = getattr(
            obj, "nu_seen", obj.notification_users.filter(seen=True).count()
        )
        unseen_users = total_users - seen_users

        if total_users > 0:
            engagement_rate = (seen_users / total_users) * 100
            avg_time_to_see = "N/A"
        else:
            engagement_rate = 0
            avg_time_to_see = "N/A"

        rate_formatted = f"{engagement_rate:.1f}%"
        performance = (
            "Excellent"
            if engagement_rate > 75
            else "Good"
            if engagement_rate > 50
            else "Poor"
        )

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Total Recipients:</strong></div><div>{total}</div>"
            "<div><strong>Seen:</strong></div><div>{seen}</div>"
            "<div><strong>Unseen:</strong></div><div>{unseen}</div>"
            "<div><strong>Engagement Rate:</strong></div><div>{rate}</div>"
            "<div><strong>Avg. Time to See:</strong></div><div>{avg_time}</div>"
            "<div><strong>Performance:</strong></div><div>{performance}</div>"
            "</div>"
            "</div>",
            total=total_users,
            seen=seen_users,
            unseen=unseen_users,
            rate=rate_formatted,
            avg_time=avg_time_to_see,
            performance=performance,
        )

    @admin.display(description=_("Timing Summary"))
    def timing_summary(self, obj):
        now = timezone.now()
        created_age = now - obj.created_at

        is_expired = obj.expiry_date and now > obj.expiry_date

        is_business_hours = 9 <= obj.created_at.hour <= 17
        is_weekend = obj.created_at.weekday() >= 5

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Created:</strong></div><div>{created}</div>"
            "<div><strong>Age:</strong></div><div>{days}d {hours}h</div>"
            "<div><strong>Expires:</strong></div><div>{expiry}</div>"
            "<div><strong>Status:</strong></div><div>{status}</div>"
            "<div><strong>Business Hours:</strong></div><div>{bhours}</div>"
            "<div><strong>Weekend:</strong></div><div>{weekend}</div>"
            "</div>"
            "</div>",
            created=obj.created_at.strftime("%Y-%m-%d %H:%M"),
            days=created_age.days,
            hours=created_age.seconds // 3600,
            expiry=(
                obj.expiry_date.strftime("%Y-%m-%d")
                if obj.expiry_date
                else "Never"
            ),
            status="Expired" if is_expired else "Active",
            bhours="Yes" if is_business_hours else "No",
            weekend="Yes" if is_weekend else "No",
        )


@admin.register(NotificationUser)
class NotificationUserAdmin(ModelAdmin):
    compressed_fields = True
    list_fullwidth = True
    list_filter_submit = True

    list_display = [
        "user_info",
        "notification_info",
        "seen_status",
        "priority_indicator",
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

    @admin.display(description=_("User"))
    def user_info(self, obj):
        user = obj.user
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{name}</div>'
            '<div class="text-base-600 dark:text-base-400">{email}</div>'
            '<div class="text-xs text-base-600 dark:text-base-300">ID: {id}</div>'
            "</div>",
            name=user.full_name or user.username,
            email=user.email,
            id=user.id,
        )

    @admin.display(description=_("Notification"))
    def notification_info(self, obj):
        notification = obj.notification
        title = (
            notification.safe_translation_getter("title", any_language=True)
            or "No Title"
        )
        title_display = title[:30] + "..." if len(title) > 30 else title
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{title}</div>'
            '<div class="text-base-600 dark:text-base-400">{kind}</div>'
            '<div class="text-xs text-base-600 dark:text-base-300">#{id}</div>'
            "</div>",
            title=title_display,
            kind=notification.get_kind_display(),
            id=notification.id,
        )

    @admin.display(description=_("Status"))
    def seen_status(self, obj):
        if not obj.created_at:
            return "Available after creation."

        if obj.seen:
            time_diff = ""
            if obj.seen_at:
                now = timezone.now()
                diff = now - obj.seen_at
                if diff.days > 0:
                    time_diff = f" ({diff.days}d ago)"
                else:
                    time_diff = f" ({diff.seconds // 3600}h ago)"
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200 rounded-full">'
                "✅ Seen{time_diff}"
                "</span>",
                time_diff=time_diff,
            )
        now = timezone.now()
        age = now - obj.created_at

        if age > timedelta(days=7):
            urgency_class = "bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-200"
            icon = "🚨"
        elif age > timedelta(days=1):
            urgency_class = "bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-200"
            icon = "⚠️"
        else:
            urgency_class = "bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-200"
            icon = "👁️"

        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium {cls} rounded-full">'
            "{icon} Unseen"
            "</span>",
            cls=urgency_class,
            icon=icon,
        )

    @admin.display(description=_("Priority"))
    def priority_indicator(self, obj):
        priority = obj.notification.priority

        priority_config = {
            "LOW": {"color": "text-base-500", "icon": "🔽"},
            "NORMAL": {"color": "text-blue-600", "icon": "ℹ️"},  # noqa: RUF001
            "HIGH": {"color": "text-orange-600", "icon": "⚠️"},
            "URGENT": {"color": "text-red-600", "icon": "🚨"},
            "CRITICAL": {"color": "text-purple-600", "icon": "🔴"},
        }

        config = priority_config.get(priority, priority_config["NORMAL"])
        return format_html(
            '<div class="text-sm">'
            '<div class="flex items-center gap-1 {color}">'
            "<span>{icon}</span>"
            "<span>{priority}</span>"
            "</div>"
            '<div class="text-xs text-base-600 dark:text-base-300">{category}</div>'
            "</div>",
            color=config["color"],
            icon=config["icon"],
            priority=obj.notification.get_priority_display(),
            category=obj.notification.get_category_display(),
        )

    @admin.display(description=_("Timing"))
    def timing_display(self, obj):
        if not obj.created_at:
            return "Available after creation."

        now = timezone.now()
        age = now - obj.created_at

        is_expired = (
            obj.notification.expiry_date and now > obj.notification.expiry_date
        )

        if is_expired:
            status_display = format_html(
                '<span class="text-red-600 dark:text-red-400">Expired</span>'
            )
        else:
            status_display = format_html(
                '<span class="text-green-600 dark:text-green-400">Active</span>'
            )

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{date}</div>'
            '<div class="text-base-600 dark:text-base-400">{age}d ago</div>'
            "<div>{status}</div>"
            "</div>",
            date=obj.created_at.strftime("%m-%d"),
            age=age.days,
            status=status_display,
        )

    @admin.display(description=_("Analytics"))
    def user_notification_analytics(self, obj):
        if not obj.created_at:
            return "Available after creation."

        now = timezone.now()
        age = now - obj.created_at

        response_time = "N/A"
        if obj.seen and obj.seen_at:
            response_diff = obj.seen_at - obj.created_at
            if response_diff.days > 0:
                response_time = f"{response_diff.days}d"
            else:
                hours = response_diff.seconds // 3600
                response_time = f"{hours}h"

        is_expired = (
            obj.notification.expiry_date and now > obj.notification.expiry_date
        )

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Age:</strong></div><div>{days}d {hours}h</div>"
            "<div><strong>Response Time:</strong></div><div>{response}</div>"
            "<div><strong>Notification Status:</strong></div><div>{status}</div>"
            "<div><strong>Priority Level:</strong></div><div>{priority}</div>"
            "<div><strong>Category:</strong></div><div>{category}</div>"
            "<div><strong>Engagement:</strong></div><div>{engagement}</div>"
            "</div>"
            "</div>",
            days=age.days,
            hours=age.seconds // 3600,
            response=response_time,
            status="Expired" if is_expired else "Active",
            priority=obj.notification.get_priority_display(),
            category=obj.notification.get_category_display(),
            engagement="Good" if obj.seen else "Pending",
        )
