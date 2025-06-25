from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import Group
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from parler.admin import TranslatableAdmin
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateFilter,
    RangeDateTimeFilter,
    RangeNumericListFilter,
    RelatedDropdownFilter,
)
from unfold.contrib.forms.widgets import WysiwygWidget
from unfold.decorators import action
from unfold.enums import ActionVariant
from unfold.forms import (
    AdminPasswordChangeForm,
    UserChangeForm,
    UserCreationForm,
)

from user.models import UserAccount
from user.models.address import UserAddress
from user.models.subscription import SubscriptionTopic, UserSubscription

admin.site.unregister(Group)


class SubscriptionCountFilter(RangeNumericListFilter):
    title = _("Subscription Count")
    parameter_name = "subscription_count"

    def queryset(self, request, queryset):
        filters = {}

        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        if value_from and value_from != "":
            filters["subscription_count__gte"] = value_from

        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to and value_to != "":
            filters["subscription_count__lte"] = value_to

        return queryset.filter(**filters) if filters else queryset

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class AddressCountFilter(RangeNumericListFilter):
    title = _("Address Count")
    parameter_name = "address_count"

    def queryset(self, request, queryset):
        filters = {}

        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        if value_from and value_from != "":
            filters["address_count__gte"] = value_from

        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to and value_to != "":
            filters["address_count__lte"] = value_to

        return queryset.filter(**filters) if filters else queryset

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class UserStatusFilter(DropdownFilter):
    title = _("User Status")
    parameter_name = "user_status"

    def lookups(self, request, model_admin):
        return [
            ("active_staff", _("Active Staff")),
            ("active_regular", _("Active Regular Users")),
            ("inactive", _("Inactive Users")),
            ("superuser", _("Super Users")),
            ("with_subscriptions", _("Users with Subscriptions")),
            ("no_subscriptions", _("Users without Subscriptions")),
        ]

    def queryset(self, request, queryset):
        filter_value = self.value()

        match filter_value:
            case "active_staff":
                filter_kwargs = {"is_active": True, "is_staff": True}
            case "active_regular":
                filter_kwargs = {
                    "is_active": True,
                    "is_staff": False,
                    "is_superuser": False,
                }
            case "inactive":
                filter_kwargs = {"is_active": False}
            case "superuser":
                filter_kwargs = {"is_superuser": True}
            case "with_subscriptions":
                return queryset.filter(subscriptions__isnull=False).distinct()
            case "no_subscriptions":
                filter_kwargs = {"subscriptions__isnull": True}
            case _:
                return queryset

        return queryset.filter(**filter_kwargs)


class SocialMediaFilter(DropdownFilter):
    title = _("Social Media Presence")
    parameter_name = "social_media"

    def lookups(self, request, model_admin):
        return [
            ("has_website", _("Has Website")),
            ("has_social", _("Has Social Media")),
            ("no_social", _("No Social Media")),
        ]

    def queryset(self, request, queryset):
        filter_value = self.value()

        match filter_value:
            case "has_website":
                return queryset.exclude(website="")
            case "has_social":
                return queryset.filter(
                    Q(twitter__gt="")
                    | Q(linkedin__gt="")
                    | Q(facebook__gt="")
                    | Q(instagram__gt="")
                    | Q(youtube__gt="")
                    | Q(github__gt="")
                )
            case "no_social":
                filter_kwargs = {
                    "website": "",
                    "twitter": "",
                    "linkedin": "",
                    "facebook": "",
                    "instagram": "",
                    "youtube": "",
                    "github": "",
                }
            case _:
                return queryset

        return queryset.filter(**filter_kwargs)


class TopicCategoryFilter(DropdownFilter):
    title = _("Topic Category")
    parameter_name = "topic_category"

    def lookups(self, request, model_admin):
        return SubscriptionTopic.TopicCategory.choices

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(topic__category=self.value())
        return queryset


class UserAddressInline(TabularInline):
    model = UserAddress
    extra = 0
    fields = (
        "title",
        "first_name",
        "last_name",
        "city",
        "country",
        "is_main",
        "phone",
    )
    readonly_fields = ("created_at",)
    show_change_link = True
    tab = True


class UserSubscriptionInline(TabularInline):
    model = UserSubscription
    extra = 0
    fields = ("topic", "status", "subscribed_at", "unsubscribed_at")
    readonly_fields = ("subscribed_at", "unsubscribed_at")
    show_change_link = True
    tab = True


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


@admin.register(UserAccount)
class UserAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    formfield_overrides = {
        models.TextField: {
            "widget": WysiwygWidget,
        },
    }

    list_display = [
        "id",
        "user_profile_display",
        "contact_info_display",
        "location_display",
        "user_status_badges",
        "social_links_display",
        "engagement_metrics",
        "last_activity",
        "created_at",
    ]

    list_filter = [
        UserStatusFilter,
        SocialMediaFilter,
        "is_active",
        "is_staff",
        "is_superuser",
        ("country", RelatedDropdownFilter),
        ("region", RelatedDropdownFilter),
        SubscriptionCountFilter,
        AddressCountFilter,
        ("created_at", RangeDateTimeFilter),
        ("updated_at", RangeDateTimeFilter),
        ("birth_date", RangeDateFilter),
    ]

    search_fields = [
        "email",
        "username",
        "first_name",
        "last_name",
        "phone",
        "city",
        "address",
        "bio",
    ]

    list_select_related = ["country", "region"]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "engagement_metrics",
        "social_links_summary",
        "subscription_summary",
        "address_summary",
    ]

    ordering = ["-created_at"]
    date_hierarchy = "created_at"
    save_on_top = True
    list_per_page = 25

    fieldsets = (
        (
            _("Account Credentials"),
            {
                "fields": ("email", "username", "password"),
                "classes": ("wide",),
            },
        ),
        (
            _("Personal Information"),
            {
                "fields": (
                    "first_name",
                    "last_name",
                    "phone",
                    "birth_date",
                    "image",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Bio & Description"),
            {
                "fields": ("bio",),
                "classes": ("wide",),
            },
        ),
        (
            _("Location & Address"),
            {
                "fields": (
                    "address",
                    "city",
                    "zipcode",
                    "place",
                    "country",
                    "region",
                    "address_summary",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Social Media & Website"),
            {
                "fields": (
                    "website",
                    "linkedin",
                    "github",
                    "twitter",
                    "facebook",
                    "instagram",
                    "youtube",
                    "social_links_summary",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Account Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Subscriptions & Engagement"),
            {
                "fields": ("subscription_summary", "engagement_metrics"),
                "classes": ("collapse",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": ("id", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "username", "password1", "password2"),
            },
        ),
    )

    inlines = [UserAddressInline, UserSubscriptionInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.annotate(
            subscription_count=Count("subscriptions", distinct=True),
            address_count=Count("addresses", distinct=True),
            active_subscription_count=Count(
                "subscriptions",
                filter=Q(
                    subscriptions__status=UserSubscription.SubscriptionStatus.ACTIVE
                ),
                distinct=True,
            ),
        )
        return qs

    def user_profile_display(self, obj):
        profile_html = '<div class="flex items-center gap-3">'

        if obj.image:
            profile_html += f'<img src="{obj.image.url}" class="rounded-full object-cover" style="width: 60px; height: 60px; object-fit: cover; border-radius: 8px; border: 1px solid #e5e7eb;" />'
        else:
            initials = "".join(
                [
                    name[0].upper()
                    for name in [obj.first_name, obj.last_name]
                    if name
                ]
            )[:2]
            if not initials:
                initials = obj.email[0].upper() if obj.email else "U"
            profile_html += f'<div class="rounded-full bg-blue-100 dark:bg-blue-900 flex items-center justify-center text-blue-600 dark:text-blue-300 font-medium text-sm" style="width: 60px; height: 60px; object-fit: cover; border-radius: 8px; border: 1px solid #e5e7eb;">{initials}</div>'

        full_name = obj.full_name or obj.username or "Anonymous"
        profile_html += "<div>"
        profile_html += f'<div class="font-medium text-base-900 dark:text-base-100">{full_name}</div>'
        profile_html += f'<div class="text-sm text-base-500 dark:text-base-400">{obj.email}</div>'
        profile_html += "</div></div>"

        return format_html(profile_html)

    user_profile_display.short_description = _("Profile")

    def contact_info_display(self, obj):
        contact_parts = []
        if obj.phone:
            contact_parts.append(
                f'<span class="flex items-center gap-1"><span>📞</span><span>{obj.phone}</span></span>'
            )
        if obj.username:
            contact_parts.append(
                f'<span class="flex items-center gap-1"><span>👤</span><span>{obj.username}</span></span>'
            )

        if contact_parts:
            return mark_safe(
                f'<div class="text-sm text-base-700 dark:text-base-300 space-y-1">{"".join(contact_parts)}</div>'
            )
        return format_html(
            '<span class="text-base-400 dark:text-base-500">No contact info</span>'
        )

    contact_info_display.short_description = _("Contact")

    def location_display(self, obj):
        location_parts = []
        if obj.city:
            location_parts.append(obj.city)
        if obj.country:
            location_parts.append(str(obj.country))

        if location_parts:
            return format_html(
                '<div class="text-sm text-base-700 dark:text-base-300">'
                '<span class="flex items-center gap-1">'
                "<span>📍</span><span>{}</span>"
                "</span></div>",
                ", ".join(location_parts),
            )
        return format_html(
            '<span class="text-base-400 dark:text-base-500">No location</span>'
        )

    location_display.short_description = _("Location")

    def user_status_badges(self, obj):
        badges = []

        if obj.is_superuser:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full gap-1">'
                "<span>👑</span><span>Super</span></span>"
            )
        elif obj.is_staff:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-300 rounded-full gap-1">'
                "<span>⚡</span><span>Staff</span></span>"
            )

        if obj.is_active:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full gap-1">'
                "<span>✓</span><span>Active</span></span>"
            )
        else:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full gap-1">'
                "<span>✗</span><span>Inactive</span></span>"
            )

        return mark_safe(
            f'<div class="flex flex-wrap gap-1">{"".join(badges)}</div>'
        )

    user_status_badges.short_description = _("Status")

    def social_links_display(self, obj):
        links = []
        social_fields = {
            "website": ("🌐", "Website"),
            "linkedin": ("💼", "LinkedIn"),
            "github": ("💻", "GitHub"),
            "twitter": ("🐦", "Twitter"),
        }

        for field, (icon, name) in social_fields.items():
            if getattr(obj, field):
                links.append(f'<span title="{name}">{icon}</span>')

        if links:
            return mark_safe(f'<div class="flex gap-1">{"".join(links)}</div>')
        return format_html(
            '<span class="text-base-400 dark:text-base-500">-</span>'
        )

    social_links_display.short_description = _("Social")

    def engagement_metrics(self, obj):
        subscriptions = getattr(obj, "subscription_count", 0)
        active_subs = getattr(obj, "active_subscription_count", 0)
        addresses = getattr(obj, "address_count", 0)

        return format_html(
            '<div class="text-sm text-base-700 dark:text-base-300 flex items-center gap-3">'
            '<span class="flex items-center gap-1 text-blue-600 dark:text-blue-400">'
            "<span>📧</span><span>{}/{}</span>"
            "</span>"
            '<span class="flex items-center gap-1 text-green-600 dark:text-green-400">'
            "<span>📍</span><span>{}</span>"
            "</span>"
            "</div>",
            active_subs,
            subscriptions,
            addresses,
        )

    engagement_metrics.short_description = _("Engagement")

    def last_activity(self, obj):
        return format_html(
            '<div class="text-sm text-base-600 dark:text-base-400">'
            "<div>Updated: {}</div>"
            "</div>",
            obj.updated_at.strftime("%Y-%m-%d %H:%M")
            if obj.updated_at
            else "Never",
        )

    last_activity.short_description = _("Last Activity")

    def social_links_summary(self, obj):
        links = []
        social_fields = {
            "website": "Website",
            "linkedin": "LinkedIn",
            "github": "GitHub",
            "twitter": "Twitter",
            "facebook": "Facebook",
            "instagram": "Instagram",
            "youtube": "YouTube",
        }

        for field, name in social_fields.items():
            value = getattr(obj, field)
            if value:
                links.append(
                    f'<a href="{value}" target="_blank" class="text-blue-600 dark:text-blue-400 hover:underline">{name}</a>'
                )

        if links:
            return mark_safe(
                f'<div class="space-y-1">{"<br>".join(links)}</div>'
            )
        return format_html(
            '<span class="text-base-400 dark:text-base-500 italic">No social media links</span>'
        )

    social_links_summary.short_description = _("Social Links Summary")

    def subscription_summary(self, obj):
        subscriptions = obj.subscriptions.select_related("topic").all()
        if not subscriptions:
            return format_html(
                '<span class="text-base-400 dark:text-base-500 italic">No subscriptions</span>'
            )

        active_count = sum(
            1
            for sub in subscriptions
            if sub.status == UserSubscription.SubscriptionStatus.ACTIVE
        )
        total_count = len(subscriptions)

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-700 dark:text-base-300">Active: {}/{}</div>'
            "</div>",
            active_count,
            total_count,
        )

    subscription_summary.short_description = _("Subscription Summary")

    def address_summary(self, obj):
        addresses = obj.addresses.all()
        if not addresses:
            return format_html(
                '<span class="text-base-400 dark:text-base-500 italic">No addresses</span>'
            )

        main_address = next((addr for addr in addresses if addr.is_main), None)

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-700 dark:text-base-300">Total: {}</div>'
            '<div class="text-base-500 dark:text-base-400">{}</div>'
            "</div>",
            len(addresses),
            f"Main: {main_address}" if main_address else "No main address",
        )

    address_summary.short_description = _("Address Summary")


@admin.register(UserAddress)
class UserAddressAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

    list_display = [
        "id",
        "address_display",
        "contact_person",
        "location_info",
        "main_address_badge",
        "contact_numbers",
        "created_at",
    ]

    list_filter = [
        "is_main",
        "floor",
        "location_type",
        ("country", RelatedDropdownFilter),
        ("region", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    ]

    search_fields = [
        "user__email",
        "user__username",
        "title",
        "first_name",
        "last_name",
        "street",
        "city",
    ]

    list_select_related = ["user", "country", "region"]
    readonly_fields = ["id", "created_at", "updated_at"]

    fieldsets = (
        (
            _("Address Owner"),
            {
                "fields": ("user", "title"),
                "classes": ("wide",),
            },
        ),
        (
            _("Contact Person"),
            {
                "fields": ("first_name", "last_name"),
                "classes": ("wide",),
            },
        ),
        (
            _("Address Details"),
            {
                "fields": (
                    "street",
                    "street_number",
                    "city",
                    "zipcode",
                    "country",
                    "region",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Additional Information"),
            {
                "fields": (
                    "floor",
                    "location_type",
                    "notes",
                    "is_main",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Contact Information"),
            {
                "fields": ("phone", "mobile_phone"),
                "classes": ("wide",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": ("id", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def address_display(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="text-base-500 dark:text-base-400">{}</div>'
            "</div>",
            obj.title,
            f"{obj.street} {obj.street_number}, {obj.city}",
        )

    address_display.short_description = _("Address")

    def contact_person(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-700 dark:text-base-300">{} {}</div>'
            '<div class="text-base-500 dark:text-base-400">{}</div>'
            "</div>",
            obj.first_name,
            obj.last_name,
            obj.user.email,
        )

    contact_person.short_description = _("Contact Person")

    def location_info(self, obj):
        location_parts = [obj.city]
        if obj.country:
            location_parts.append(str(obj.country))
        if obj.zipcode:
            location_parts.append(obj.zipcode)

        return format_html(
            '<div class="text-sm text-base-700 dark:text-base-300">{}</div>',
            ", ".join(location_parts),
        )

    location_info.short_description = _("Location")

    def main_address_badge(self, obj):
        if obj.is_main:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full gap-1">'
                "<span>⭐</span><span>Main</span>"
                "</span>"
            )
        return ""

    main_address_badge.short_description = _("Main")

    def contact_numbers(self, obj):
        numbers = []
        if obj.phone:
            numbers.append(
                f'<span class="flex items-center gap-1"><span>📞</span><span>{obj.phone}</span></span>'
            )
        if obj.mobile_phone:
            numbers.append(
                f'<span class="flex items-center gap-1"><span>📱</span><span>{obj.mobile_phone}</span></span>'
            )

        if numbers:
            return mark_safe(
                f'<div class="text-sm text-base-700 dark:text-base-300 space-y-1">{"".join(numbers)}</div>'
            )
        return format_html(
            '<span class="text-base-400 dark:text-base-500">No phone</span>'
        )

    contact_numbers.short_description = _("Contact Numbers")


@admin.register(SubscriptionTopic)
class SubscriptionTopicAdmin(ModelAdmin, TranslatableAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = False
    list_filter_submit = True
    list_filter_sheet = True

    list_display = [
        "name_display",
        "category_badge",
        "active_status",
        "is_active",
        "settings_badges",
        "subscriber_metrics",
        "created_at",
    ]

    list_filter = [
        "category",
        "is_active",
        "is_default",
        "requires_confirmation",
        ("created_at", RangeDateTimeFilter),
    ]

    search_fields = ["translations__name", "slug", "translations__description"]
    readonly_fields = ["uuid", "created_at", "updated_at", "subscriber_metrics"]

    fieldsets = (
        (
            _("Topic Information"),
            {
                "fields": ("name", "slug", "description", "category"),
                "classes": ("wide",),
            },
        ),
        (
            _("Settings"),
            {
                "fields": (
                    "is_active",
                    "is_default",
                    "requires_confirmation",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": (
                    "uuid",
                    "created_at",
                    "updated_at",
                    "subscriber_metrics",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.annotate(
            active_subscribers=Count(
                "subscribers",
                filter=Q(
                    subscribers__status=UserSubscription.SubscriptionStatus.ACTIVE
                ),
            ),
            total_subscribers=Count("subscribers"),
        )
        return qs

    def name_display(self, obj):
        name = (
            obj.safe_translation_getter("name", any_language=True)
            or "Unnamed Topic"
        )
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="text-base-500 dark:text-base-400">{}</div>'
            "</div>",
            name,
            obj.slug,
        )

    name_display.short_description = _("Topic")

    def category_badge(self, obj):
        colors = {
            "MARKETING": "bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-300",
            "PRODUCT": "bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300",
            "ACCOUNT": "bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300",
            "SYSTEM": "bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300",
            "NEWSLETTER": "bg-indigo-50 dark:bg-indigo-900 text-indigo-700 dark:text-indigo-300",
            "OTHER": "bg-base-50 dark:bg-base-900 text-base-700 dark:text-base-300",
        }
        color_class = colors.get(obj.category, colors["OTHER"])

        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium {} rounded-full">{}</span>',
            color_class,
            obj.get_category_display(),
        )

    category_badge.short_description = _("Category")

    def active_status(self, obj):
        if obj.is_active:
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full gap-1">'
                "<span>✓</span><span>Active</span>"
                "</span>"
            )
        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full gap-1">'
            "<span>✗</span><span>Inactive</span>"
            "</span>"
        )

    active_status.short_description = _("Status")

    def settings_badges(self, obj):
        badges = []
        if obj.is_default:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full gap-1">'
                "<span>⭐</span><span>Default</span></span>"
            )
        if obj.requires_confirmation:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full gap-1">'
                "<span>✉️</span><span>Confirm</span></span>"
            )

        return (
            mark_safe(
                f'<div class="flex flex-wrap gap-1">{"".join(badges)}</div>'
            )
            if badges
            else ""
        )

    settings_badges.short_description = _("Settings")

    def subscriber_metrics(self, obj):
        active = getattr(obj, "active_subscribers", 0)
        total = getattr(obj, "total_subscribers", 0)

        return format_html(
            '<div class="text-sm text-base-700 dark:text-base-300 flex items-center gap-3">'
            '<span class="flex items-center gap-1 text-green-600 dark:text-green-400">'
            "<span>✓</span><span>{}</span>"
            "</span>"
            '<span class="flex items-center gap-1 text-blue-600 dark:text-blue-400">'
            "<span>👥</span><span>{}</span>"
            "</span>"
            "</div>",
            active,
            total,
        )

    subscriber_metrics.short_description = _("Subscribers")


@admin.register(UserSubscription)
class UserSubscriptionAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

    list_display = [
        "subscription_info",
        "user_info",
        "topic_info",
        "status_display",
        "status",
        "subscription_dates",
        "created_at",
    ]

    list_filter = [
        "status",
        TopicCategoryFilter,
        ("topic", RelatedDropdownFilter),
        ("subscribed_at", RangeDateTimeFilter),
        ("unsubscribed_at", RangeDateTimeFilter),
    ]

    search_fields = [
        "user__email",
        "user__username",
        "topic__translations__name",
        "user__first_name",
        "user__last_name",
    ]

    readonly_fields = [
        "subscribed_at",
        "unsubscribed_at",
        "created_at",
        "updated_at",
    ]

    raw_id_fields = ["user"]
    autocomplete_fields = ["topic"]
    list_select_related = ["user", "topic"]

    actions = [
        "activate_subscriptions",
        "deactivate_subscriptions",
    ]

    fieldsets = (
        (
            _("Subscription Details"),
            {
                "fields": ("user", "topic", "status"),
                "classes": ("wide",),
            },
        ),
        (
            _("Timestamps"),
            {
                "fields": (
                    "subscribed_at",
                    "unsubscribed_at",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Additional Data"),
            {
                "fields": ("confirmation_token", "metadata"),
                "classes": ("collapse",),
            },
        ),
    )

    def subscription_info(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">Subscription #{}</div>'
            '<div class="text-base-500 dark:text-base-400">{}</div>'
            "</div>",
            obj.id,
            obj.created_at.strftime("%Y-%m-%d"),
        )

    subscription_info.short_description = _("Subscription")

    def user_info(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-700 dark:text-base-300">{}</div>'
            '<div class="text-base-500 dark:text-base-400">{}</div>'
            "</div>",
            obj.user.full_name or obj.user.username or "Anonymous",
            obj.user.email,
        )

    user_info.short_description = _("User")

    def topic_info(self, obj):
        topic_name = (
            obj.topic.safe_translation_getter("name", any_language=True)
            or "Unnamed Topic"
        )
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-700 dark:text-base-300">{}</div>'
            '<div class="text-base-500 dark:text-base-400">{}</div>'
            "</div>",
            topic_name,
            obj.topic.get_category_display(),
        )

    topic_info.short_description = _("Topic")

    def status_display(self, obj):
        colors = {
            UserSubscription.SubscriptionStatus.ACTIVE: "bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300",
            UserSubscription.SubscriptionStatus.PENDING: "bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300",
            UserSubscription.SubscriptionStatus.UNSUBSCRIBED: "bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300",
            UserSubscription.SubscriptionStatus.BOUNCED: "bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-300",
        }

        icons = {
            UserSubscription.SubscriptionStatus.ACTIVE: "✓",
            UserSubscription.SubscriptionStatus.PENDING: "⏳",
            UserSubscription.SubscriptionStatus.UNSUBSCRIBED: "✗",
            UserSubscription.SubscriptionStatus.BOUNCED: "⚠️",
        }

        color_class = colors.get(
            obj.status, colors[UserSubscription.SubscriptionStatus.ACTIVE]
        )
        icon = icons.get(obj.status, "?")

        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium {} rounded-full gap-1">'
            "<span>{}</span><span>{}</span>"
            "</span>",
            color_class,
            icon,
            obj.get_status_display(),
        )

    status_display.short_description = _("Status")

    def subscription_dates(self, obj):
        dates_html = '<div class="text-sm text-base-600 dark:text-base-400">'
        dates_html += f"<div>Subscribed: {obj.subscribed_at.strftime('%Y-%m-%d %H:%M')}</div>"
        if obj.unsubscribed_at:
            dates_html += f"<div>Unsubscribed: {obj.unsubscribed_at.strftime('%Y-%m-%d %H:%M')}</div>"
        dates_html += "</div>"

        return format_html(dates_html)

    subscription_dates.short_description = _("Dates")

    @action(
        description=_("Activate selected subscriptions"),
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def activate_subscriptions(self, request, queryset):
        updated = queryset.filter(
            status__in=[
                UserSubscription.SubscriptionStatus.PENDING,
                UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
            ]
        ).update(
            status=UserSubscription.SubscriptionStatus.ACTIVE,
            unsubscribed_at=None,
        )
        self.message_user(
            request,
            _("%(count)d subscriptions were activated.") % {"count": updated},
        )

    @action(
        description=_("Deactivate selected subscriptions"),
        variant=ActionVariant.WARNING,
        icon="cancel",
    )
    def deactivate_subscriptions(self, request, queryset):
        updated = queryset.filter(
            status=UserSubscription.SubscriptionStatus.ACTIVE
        ).update(
            status=UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
            unsubscribed_at=timezone.now(),
        )
        self.message_user(
            request,
            _("%(count)d subscriptions were deactivated.") % {"count": updated},
        )
