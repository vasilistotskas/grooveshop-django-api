from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import Group
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.forms import (
    AdminPasswordChangeForm,
    UserChangeForm,
    UserCreationForm,
)

from user.models import UserAccount
from user.models.address import UserAddress
from user.models.subscription import SubscriptionTopic, UserSubscription

admin.site.unregister(Group)


@admin.register(UserAccount)
class UserAdmin(ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    list_display = [
        "email",
        "username",
        "first_name",
        "last_name",
        "phone",
        "country",
        "region",
        "is_active",
        "is_staff",
    ]
    list_filter = ["is_active", "is_staff", "is_superuser", "country", "region"]
    search_fields = [
        "email",
        "username",
        "phone",
        "first_name",
        "last_name",
        "city",
        "address",
    ]
    ordering = ["-created_at"]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            _("Personal info"),
            {
                "fields": (
                    "username",
                    "first_name",
                    "last_name",
                    "phone",
                    "birth_date",
                    "bio",
                    "image",
                )
            },
        ),
        (
            _("Contact info"),
            {
                "fields": (
                    "address",
                    "city",
                    "zipcode",
                    "place",
                    "country",
                    "region",
                ),
            },
        ),
        (
            _("Social media"),
            {
                "fields": (
                    "website",
                    "twitter",
                    "facebook",
                    "instagram",
                    "linkedin",
                    "youtube",
                    "github",
                ),
            },
        ),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (_("Important dates"), {"fields": ("created_at", "updated_at")}),
    )

    readonly_fields = ["created_at", "updated_at"]

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "username"),
            },
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(self.readonly_fields)
        if obj:
            readonly_fields.extend(["created_at", "updated_at"])
        return readonly_fields


@admin.register(UserAddress)
class UserAddressAdmin(ModelAdmin):
    list_display = [
        "user",
        "title",
        "first_name",
        "last_name",
        "street",
        "street_number",
        "city",
        "zipcode",
        "country",
        "region",
        "floor",
        "location_type",
        "phone",
        "mobile_phone",
        "notes",
        "is_main",
    ]
    list_filter = ["floor", "location_type"]
    search_fields = [
        "user__email",
        "user__username",
        "title",
        "first_name",
        "last_name",
        "street",
    ]


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


@admin.register(SubscriptionTopic)
class SubscriptionTopicAdmin(ModelAdmin):
    list_display = [
        "name",
        "slug",
        "category",
        "is_active",
        "is_default",
        "requires_confirmation",
        "subscriber_count",
        "created_at",
    ]
    list_filter = [
        "category",
        "is_active",
        "is_default",
        "requires_confirmation",
        "created_at",
    ]
    search_fields = ["translations__name", "slug", "translations__description"]
    readonly_fields = ["uuid", "created_at", "updated_at", "subscriber_count"]

    fieldsets = (
        (
            _("Basic Information"),
            {"fields": ("name", "slug", "description", "category")},
        ),
        (
            _("Settings"),
            {
                "fields": (
                    "is_active",
                    "is_default",
                    "requires_confirmation",
                )
            },
        ),
        (
            _("Metadata"),
            {
                "fields": (
                    "uuid",
                    "created_at",
                    "updated_at",
                    "subscriber_count",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def subscriber_count(self, obj):
        count = obj.subscribers.filter(
            status=UserSubscription.SubscriptionStatus.ACTIVE
        ).count()
        return format_html(
            '<span style="color: green; font-weight: bold;">{}</span>', count
        )

    subscriber_count.short_description = _("Active Subscribers")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.annotate(
            active_count=Count(
                "subscribers",
                filter=Q(
                    subscribers__status=UserSubscription.SubscriptionStatus.ACTIVE
                ),
            )
        )
        return qs


@admin.register(UserSubscription)
class UserSubscriptionAdmin(ModelAdmin):
    list_display = [
        "user",
        "topic",
        "status",
        "status_badge",
        "subscribed_at",
        "unsubscribed_at",
    ]
    list_filter = [
        "status",
        "topic__category",
        "subscribed_at",
        "unsubscribed_at",
    ]
    search_fields = [
        "user__email",
        "user__username",
        "topic__name",
    ]
    readonly_fields = [
        "subscribed_at",
        "unsubscribed_at",
        "created_at",
        "updated_at",
    ]
    raw_id_fields = ["user"]
    autocomplete_fields = ["topic"]

    fieldsets = (
        (_("Subscription Details"), {"fields": ("user", "topic", "status")}),
        (
            _("Timestamps"),
            {
                "fields": (
                    "subscribed_at",
                    "unsubscribed_at",
                    "created_at",
                    "updated_at",
                )
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

    def status_badge(self, obj):
        colors = {
            UserSubscription.SubscriptionStatus.ACTIVE: "green",
            UserSubscription.SubscriptionStatus.PENDING: "orange",
            UserSubscription.SubscriptionStatus.UNSUBSCRIBED: "red",
            UserSubscription.SubscriptionStatus.BOUNCED: "darkred",
        }
        color = colors.get(obj.status, "gray")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display(),
        )

    status_badge.short_description = _("Status")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.select_related("user", "topic")
        return qs

    actions = ["activate_subscriptions", "deactivate_subscriptions"]

    @admin.action(description=_("Activate selected subscriptions"))
    def activate_subscriptions(self, request, queryset):
        count = queryset.filter(
            status__in=[
                UserSubscription.SubscriptionStatus.PENDING,
                UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
            ]
        ).update(
            status=UserSubscription.SubscriptionStatus.ACTIVE,
            unsubscribed_at=None,
        )
        self.message_user(
            request, _("{} subscriptions activated.").format(count)
        )

    @admin.action(description=_("Deactivate selected subscriptions"))
    def deactivate_subscriptions(self, request, queryset):
        count = queryset.filter(
            status=UserSubscription.SubscriptionStatus.ACTIVE
        ).update(
            status=UserSubscription.SubscriptionStatus.UNSUBSCRIBED,
            unsubscribed_at=timezone.now(),
        )
        self.message_user(
            request, _("{} subscriptions deactivated.").format(count)
        )
