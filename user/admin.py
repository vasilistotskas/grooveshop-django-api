from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import Group
from django.db import models, transaction
from django.db.models import Count, Q
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import conditional_escape
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

from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction
from loyalty.services import LoyaltyService
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
        if value_from:
            filters["subscription_count__gte"] = value_from

        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to:
            filters["subscription_count__lte"] = value_to

        return queryset.filter(**filters) if filters else queryset

    def expected_parameters(self):
        return [f"{self.parameter_name}_from", f"{self.parameter_name}_to"]


class AddressCountFilter(RangeNumericListFilter):
    title = _("Address Count")
    parameter_name = "address_count"

    def queryset(self, request, queryset):
        filters = {}

        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        if value_from:
            filters["address_count__gte"] = value_from

        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to:
            filters["address_count__lte"] = value_to

        return queryset.filter(**filters) if filters else queryset

    def expected_parameters(self):
        return [f"{self.parameter_name}_from", f"{self.parameter_name}_to"]


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
        v = self.value()
        if v == "active_staff":
            return queryset.filter(is_active=True, is_staff=True)
        if v == "active_regular":
            return queryset.filter(
                is_active=True, is_staff=False, is_superuser=False
            )
        if v == "inactive":
            return queryset.filter(is_active=False)
        if v == "superuser":
            return queryset.filter(is_superuser=True)
        if v == "with_subscriptions":
            return queryset.filter(subscriptions__isnull=False).distinct()
        if v == "no_subscriptions":
            return queryset.filter(subscriptions__isnull=True)
        return queryset


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
        v = self.value()
        if v == "has_website":
            return queryset.exclude(website="")
        if v == "has_social":
            return queryset.filter(
                Q(twitter__gt="")
                | Q(linkedin__gt="")
                | Q(facebook__gt="")
                | Q(instagram__gt="")
                | Q(youtube__gt="")
                | Q(github__gt="")
            )
        if v == "no_social":
            return queryset.filter(
                website="",
                twitter="",
                linkedin="",
                facebook="",
                instagram="",
                youtube="",
                github="",
            )
        return queryset


class TopicCategoryFilter(DropdownFilter):
    title = _("Topic Category")
    parameter_name = "topic_category"

    def lookups(self, request, model_admin):
        return SubscriptionTopic.TopicCategory.choices

    def queryset(self, request, queryset):
        v = self.value()
        return queryset.filter(topic__category=v) if v else queryset


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

    formfield_overrides = {models.TextField: {"widget": WysiwygWidget}}

    list_display = [
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
        "loyalty_points_balance",
        "loyalty_total_xp",
        "loyalty_level",
        "loyalty_tier_name",
    ]

    ordering = ["-created_at"]
    date_hierarchy = "created_at"
    save_on_top = True
    list_per_page = 25

    fieldsets = (
        (
            _("Account Credentials"),
            {"fields": ("email", "username", "password"), "classes": ("wide",)},
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
        (_("Bio & Description"), {"fields": ("bio",), "classes": ("wide",)}),
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
            _("Loyalty & Rewards"),
            {
                "fields": (
                    "loyalty_points_balance",
                    "loyalty_total_xp",
                    "loyalty_level",
                    "loyalty_tier_name",
                ),
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
        return qs.annotate(
            subscription_count=Count("subscriptions", distinct=True),
            address_count=Count("addresses", distinct=True),
            active_subscription_count=Count(
                "subscriptions",
                filter=Q(
                    subscriptions__status=(
                        UserSubscription.SubscriptionStatus.ACTIVE
                    )
                ),
                distinct=True,
            ),
        ).prefetch_related("subscriptions__topic", "addresses")

    @admin.display(description=_("Profile"))
    def user_profile_display(self, obj):
        html = '<div class="flex items-center gap-3">'
        if obj.image:
            url = conditional_escape(obj.image.url)
            html += f'<img src="{url}" class="rounded-full object-cover" style="width:60px;height:60px;object-fit:cover;border-radius:8px;border:1px solid #e5e7eb" />'
        else:
            initials = "".join(
                [n[0].upper() for n in [obj.first_name, obj.last_name] if n]
            )[:2] or (obj.email[0].upper() if obj.email else "U")
            esc_init = conditional_escape(initials)
            html += f'<div class="rounded-full bg-blue-100 dark:bg-blue-900 flex items-center justify-center text-blue-600 dark:text-blue-300 font-medium text-sm" style="width:60px;height:60px;object-fit:cover;border-radius:8px;border:1px solid #e5e7eb">{esc_init}</div>'
        full_name = obj.full_name or obj.username or "Anonymous"
        esc_name = conditional_escape(full_name)
        esc_email = conditional_escape(obj.email or "")
        html += f'<div><div class="font-medium text-base-900 dark:text-base-100">{esc_name}</div>'
        html += f'<div class="text-sm text-base-600 dark:text-base-300">{esc_email}</div></div></div>'
        return mark_safe(html)

    @admin.display(description=_("Contact"))
    def contact_info_display(self, obj):
        parts = []
        if obj.phone:
            esc_phone = conditional_escape(obj.phone)
            parts.append(
                f'<span class="flex items-center gap-1"><span>📞</span><span>{esc_phone}</span></span>'
            )
        if obj.username:
            esc_user = conditional_escape(obj.username)
            parts.append(
                f'<span class="flex items-center gap-1"><span>👤</span><span>{esc_user}</span></span>'
            )
        if parts:
            html = (
                '<div class="text-sm text-base-700 dark:text-base-300 space-y-1">'
                + "".join(parts)
                + "</div>"
            )
            return mark_safe(html)
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300">No contact info</span>'
        )

    @admin.display(description=_("Location"))
    def location_display(self, obj):
        loc = []
        if obj.city:
            loc.append(conditional_escape(obj.city))
        if obj.country:
            loc.append(conditional_escape(str(obj.country)))
        if loc:
            text = ", ".join(loc)
            html = f'<div class="text-sm text-base-700 dark:text-base-300"><span class="flex items-center gap-1"><span>📍</span><span>{text}</span></span></div>'
            return mark_safe(html)
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300">No location</span>'
        )

    @admin.display(description=_("Status"))
    def user_status_badges(self, obj):
        badges = []
        if obj.is_superuser:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full gap-1"><span>👑</span><span>Super</span></span>'
            )
        elif obj.is_staff:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-300 rounded-full gap-1"><span>⚡</span><span>Staff</span></span>'
            )
        if obj.is_active:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full gap-1"><span>✓</span><span>Active</span></span>'
            )
        else:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full gap-1"><span>✗</span><span>Inactive</span></span>'
            )
        return mark_safe(
            '<div class="flex flex-wrap gap-1">' + "".join(badges) + "</div>"
        )

    @admin.display(description=_("Social"))
    def social_links_display(self, obj):
        icons = []
        for field, (icon, icon_name) in {
            "website": ("🌐", "Website"),
            "linkedin": ("💼", "LinkedIn"),
            "github": ("💻", "GitHub"),
            "twitter": ("🐦", "Twitter"),
        }.items():
            val = getattr(obj, field)
            if val:
                icons.append(conditional_escape(icon))
        if icons:
            return mark_safe(
                '<div class="flex gap-1">' + "".join(icons) + "</div>"
            )
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300">-</span>'
        )

    @admin.display(description=_("Engagement"))
    def engagement_metrics(self, obj):
        subs = getattr(obj, "subscription_count", 0)
        active = getattr(obj, "active_subscription_count", 0)
        addrs = getattr(obj, "address_count", 0)
        esc_active = conditional_escape(str(active))
        esc_subs = conditional_escape(str(subs))
        esc_addrs = conditional_escape(str(addrs))
        html = (
            '<div class="text-sm text-base-700 dark:text-base-300 flex items-center gap-3">'
            f'<span class="flex items-center gap-1 text-blue-600 dark:text-blue-400"><span>📧</span><span>{esc_active}/{esc_subs}</span></span>'
            f'<span class="flex items-center gap-1 text-green-600 dark:text-green-400"><span>📍</span><span>{esc_addrs}</span></span>'
            "</div>"
        )
        return mark_safe(html)

    @admin.display(description=_("Last Activity"))
    def last_activity(self, obj):
        ts = (
            obj.updated_at.strftime("%Y-%m-%d %H:%M")
            if obj.updated_at
            else "Never"
        )
        esc_ts = conditional_escape(ts)
        html = f'<div class="text-sm text-base-600 dark:text-base-400"><div>Updated: {esc_ts}</div></div>'
        return mark_safe(html)

    @admin.display(description=_("Social Links Summary"))
    def social_links_summary(self, obj):
        links = []
        fields = {
            "website": "Website",
            "linkedin": "LinkedIn",
            "github": "GitHub",
            "twitter": "Twitter",
            "facebook": "Facebook",
            "instagram": "Instagram",
            "youtube": "YouTube",
        }
        for fld, name in fields.items():
            url = getattr(obj, fld)
            if url:
                esc_url = conditional_escape(url)
                links.append(
                    f'<a href="{esc_url}" target="_blank" class="text-blue-600 dark:text-blue-400 hover:underline">{conditional_escape(name)}</a>'
                )
        if links:
            return mark_safe(
                '<div class="space-y-1">' + "<br>".join(links) + "</div>"
            )
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300 italic">No social media links</span>'
        )

    @admin.display(description=_("Subscription Summary"))
    def subscription_summary(self, obj):
        subs = list(obj.subscriptions.select_related("topic").all())
        if not subs:
            return mark_safe(
                '<span class="text-base-600 dark:text-base-300 italic">No subscriptions</span>'
            )
        active = sum(
            1
            for s in subs
            if s.status == UserSubscription.SubscriptionStatus.ACTIVE
        )
        total = len(subs)
        esc_active = conditional_escape(str(active))
        esc_total = conditional_escape(str(total))
        html = f'<div class="text-sm"><div class="font-medium text-base-700 dark:text-base-300">Active: {esc_active}/{esc_total}</div></div>'
        return mark_safe(html)

    @admin.display(description=_("Address Summary"))
    def address_summary(self, obj):
        addrs = list(obj.addresses.all())
        if not addrs:
            return mark_safe(
                '<span class="text-base-600 dark:text-base-300 italic">No addresses</span>'
            )
        main = next((a for a in addrs if a.is_main), None)
        esc_total = conditional_escape(str(len(addrs)))
        main_text = conditional_escape(str(main)) if main else "No main address"
        html = f'<div class="text-sm"><div class="font-medium text-base-700 dark:text-base-300">Total: {esc_total}</div><div class="text-base-600 dark:text-base-300">Main: {main_text}</div></div>'
        return mark_safe(html)

    @admin.display(description=_("Points Balance"))
    def loyalty_points_balance(self, obj):
        balance = LoyaltyService.get_user_balance(obj)
        esc_balance = conditional_escape(str(balance))
        html = (
            f'<span class="inline-flex items-center px-3 py-1 text-sm font-semibold'
            f" bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300"
            f' rounded-full gap-1"><span>🪙</span><span>{esc_balance} pts</span></span>'
        )
        return mark_safe(html)

    @admin.display(description=_("Total XP"))
    def loyalty_total_xp(self, obj):
        esc_xp = conditional_escape(str(obj.total_xp))
        html = (
            f'<span class="inline-flex items-center px-3 py-1 text-sm font-semibold'
            f" bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300"
            f' rounded-full gap-1"><span>⭐</span><span>{esc_xp} XP</span></span>'
        )
        return mark_safe(html)

    @admin.display(description=_("Level"))
    def loyalty_level(self, obj):
        level = LoyaltyService.get_user_level(obj)
        esc_level = conditional_escape(str(level))
        html = (
            f'<span class="inline-flex items-center px-3 py-1 text-sm font-semibold'
            f" bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300"
            f' rounded-full gap-1"><span>📊</span><span>Level {esc_level}</span></span>'
        )
        return mark_safe(html)

    @admin.display(description=_("Tier"))
    def loyalty_tier_name(self, obj):
        tier = LoyaltyService.get_user_tier(obj)
        if tier:
            esc_name = conditional_escape(str(tier))
            html = (
                f'<span class="inline-flex items-center px-3 py-1 text-sm font-semibold'
                f" bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-300"
                f' rounded-full gap-1"><span>🏆</span><span>{esc_name}</span></span>'
            )
        else:
            html = '<span class="text-base-600 dark:text-base-300 italic">No tier</span>'
        return mark_safe(html)

    @action(
        description=str(_("Adjust loyalty points for this user")),
        permissions=("change",),
        variant=ActionVariant.INFO,
        icon="loyalty",
    )
    def adjust_loyalty_points(self, request, object_id):
        """Award a flat points adjustment to the user on this change page.

        Unfold detail-action signature is ``(self, request, object_id)``
        — the URL pattern is ``<path:object_id>/<action>/``. Older code
        took ``queryset`` and iterated it, which silently iterated the
        id string character-by-character and awarded points to the
        wrong users.

        ``points_amount`` may be provided via POST or GET; defaults to
        100 when absent. Only superusers may call this action.
        """
        change_url = reverse("admin:user_useraccount_change", args=[object_id])

        if not request.user.is_superuser:
            messages.error(
                request,
                _("Only superusers may adjust loyalty points directly."),
            )
            return redirect(change_url)

        raw_amount = (
            request.POST.get("points_amount")
            or request.GET.get("points_amount")
            or "100"
        )
        description = (
            request.POST.get("description")
            or request.GET.get("description")
            or "Manual admin adjustment"
        )
        try:
            points_amount = int(raw_amount)
        except (ValueError, TypeError):
            messages.error(
                request,
                _("Invalid points amount: %(val)s") % {"val": raw_amount},
            )
            return redirect(change_url)

        if not (-10000 <= points_amount <= 10000):
            messages.error(
                request,
                _(
                    "Points amount %(val)d is out of range "
                    "(must be between -10000 and 10000)."
                )
                % {"val": points_amount},
            )
            return redirect(change_url)

        try:
            user = UserAccount.objects.get(pk=object_id)
        except (UserAccount.DoesNotExist, ValueError, TypeError):
            messages.error(request, _("User not found."))
            return redirect(change_url)

        PointsTransaction.objects.create(
            user=user,
            points=points_amount,
            transaction_type=TransactionType.ADJUST,
            description=description,
            created_by=request.user,
        )
        messages.warning(
            request,
            _(
                "Admin adjustment of %(points)d points applied to %(user)s. "
                "This action is logged and cannot be undone."
            )
            % {"points": points_amount, "user": user},
        )
        self.message_user(
            request,
            _("%(user)s received a %(points)d loyalty points adjustment.")
            % {"user": user, "points": points_amount},
        )
        return redirect(change_url)

    actions_detail = ["adjust_loyalty_points"]


@admin.register(UserAddress)
class UserAddressAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

    list_display = [
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
            {"fields": ("user", "title"), "classes": ("wide",)},
        ),
        (
            _("Contact Person"),
            {"fields": ("first_name", "last_name"), "classes": ("wide",)},
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
                "fields": ("floor", "location_type", "notes", "is_main"),
                "classes": ("wide",),
            },
        ),
        (
            _("Contact Information"),
            {"fields": ("phone",), "classes": ("wide",)},
        ),
        (
            _("System Information"),
            {
                "fields": ("id", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description=_("Address"))
    def address_display(self, obj):
        esc_title = conditional_escape(obj.title)
        addr = f"{obj.street} {obj.street_number}, {obj.city}"
        esc_addr = conditional_escape(addr)
        html = f'<div class="text-sm"><div class="font-medium text-base-900 dark:text-base-100">{esc_title}</div><div class="text-base-600 dark:text-base-300">{esc_addr}</div></div>'
        return mark_safe(html)

    @admin.display(description=_("Contact Person"))
    def contact_person(self, obj):
        esc_name = conditional_escape(f"{obj.first_name} {obj.last_name}")
        esc_email = conditional_escape(obj.user.email)
        html = f'<div class="text-sm"><div class="font-medium text-base-700 dark:text-base-300">{esc_name}</div><div class="text-base-600 dark:text-base-300">{esc_email}</div></div>'
        return mark_safe(html)

    @admin.display(description=_("Location"))
    def location_info(self, obj):
        parts = [conditional_escape(obj.city)]
        if obj.country:
            parts.append(conditional_escape(str(obj.country)))
        if obj.zipcode:
            parts.append(conditional_escape(obj.zipcode))
        text = ", ".join(parts)
        html = f'<div class="text-sm text-base-700 dark:text-base-300">{text}</div>'
        return mark_safe(html)

    @admin.display(description=_("Main"))
    def main_address_badge(self, obj):
        if obj.is_main:
            return mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full gap-1"><span>⭐</span><span>Main</span></span>'
            )
        return ""

    @admin.display(description=_("Contact Numbers"))
    def contact_numbers(self, obj):
        nums = []
        if obj.phone:
            esc_phone = conditional_escape(obj.phone)
            nums.append(
                f'<span class="flex items-center gap-1"><span>📞</span><span>{esc_phone}</span></span>'
            )
        if nums:
            html = (
                '<div class="text-sm text-base-700 dark:text-base-300 space-y-1">'
                + "".join(nums)
                + "</div>"
            )
            return mark_safe(html)
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300">No phone</span>'
        )


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
                "fields": ("is_active", "is_default", "requires_confirmation"),
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
        return qs.annotate(
            active_subscribers=Count(
                "subscribers",
                filter=Q(
                    subscribers__status=UserSubscription.SubscriptionStatus.ACTIVE
                ),
            ),
            total_subscribers=Count("subscribers"),
        )

    @admin.display(description=_("Topic"))
    def name_display(self, obj):
        name = (
            obj.safe_translation_getter("name", any_language=True)
            or "Unnamed Topic"
        )
        esc_name = conditional_escape(name)
        esc_slug = conditional_escape(obj.slug)
        html = f'<div class="text-sm"><div class="font-medium text-base-900 dark:text-base-100">{esc_name}</div><div class="text-base-600 dark:text-base-300">{esc_slug}</div></div>'
        return mark_safe(html)

    @admin.display(description=_("Category"))
    def category_badge(self, obj):
        colors = {
            "MARKETING": "bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-300",
            "PRODUCT": "bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300",
            "ACCOUNT": "bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300",
            "SYSTEM": "bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300",
            "NEWSLETTER": "bg-indigo-50 dark:bg-indigo-900 text-indigo-700 dark:text-indigo-300",
            "OTHER": "bg-base-50 dark:bg-base-900 text-base-700 dark:text-base-300",
        }
        cls = conditional_escape(colors.get(obj.category, colors["OTHER"]))
        disp = conditional_escape(obj.get_category_display())
        html = f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium {cls} rounded-full">{disp}</span>'
        return mark_safe(html)

    @admin.display(description=_("Status"))
    def active_status(self, obj):
        if obj.is_active:
            html = '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full gap-1"><span>✓</span><span>Active</span></span>'
        else:
            html = '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full gap-1"><span>✗</span><span>Inactive</span></span>'
        return mark_safe(html)

    @admin.display(description=_("Settings"))
    def settings_badges(self, obj):
        badges = []
        if obj.is_default:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full gap-1"><span>⭐</span><span>Default</span></span>'
            )
        if obj.requires_confirmation:
            badges.append(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full gap-1"><span>✉️</span><span>Confirm</span></span>'
            )
        return (
            mark_safe(
                '<div class="flex flex-wrap gap-1">'
                + "".join(badges)
                + "</div>"
            )
            if badges
            else ""
        )

    @admin.display(description=_("Subscribers"))
    def subscriber_metrics(self, obj):
        active = conditional_escape(str(getattr(obj, "active_subscribers", 0)))
        total = conditional_escape(str(getattr(obj, "total_subscribers", 0)))
        html = f'<div class="text-sm text-base-700 dark:text-base-300 flex items-center gap-3"><span class="flex items-center gap-1 text-green-600 dark:text-green-400"><span>✓</span><span>{active}</span></span><span class="flex items-center gap-1 text-blue-600 dark:text-blue-400"><span>👥</span><span>{total}</span></span></div>'
        return mark_safe(html)


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
        "confirmation_token",
    ]
    raw_id_fields = ["user"]
    autocomplete_fields = ["topic"]
    list_select_related = ["user", "topic"]

    actions = ["activate_subscriptions", "deactivate_subscriptions"]

    fieldsets = (
        (
            _("Subscription Details"),
            {"fields": ("user", "topic", "status"), "classes": ("wide",)},
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

    @admin.display(description=_("Subscription"))
    def subscription_info(self, obj):
        esc_id = conditional_escape(str(obj.id))
        date = conditional_escape(obj.created_at.strftime("%Y-%m-%d"))
        html = f'<div class="text-sm"><div class="font-medium text-base-900 dark:text-base-100">Subscription #{esc_id}</div><div class="text-base-600 dark:text-base-300">{date}</div></div>'
        return mark_safe(html)

    @admin.display(description=_("User"))
    def user_info(self, obj):
        name = obj.user.full_name or obj.user.username or "Anonymous"
        esc_name = conditional_escape(name)
        esc_email = conditional_escape(obj.user.email)
        html = f'<div class="text-sm"><div class="font-medium text-base-700 dark:text-base-300">{esc_name}</div><div class="text-base-600 dark:text-base-300">{esc_email}</div></div>'
        return mark_safe(html)

    @admin.display(description=_("Topic"))
    def topic_info(self, obj):
        topic = (
            obj.topic.safe_translation_getter("name", any_language=True)
            or "Unnamed Topic"
        )
        esc_topic = conditional_escape(topic)
        esc_cat = conditional_escape(obj.topic.get_category_display())
        html = f'<div class="text-sm"><div class="font-medium text-base-700 dark:text-base-300">{esc_topic}</div><div class="text-base-600 dark:text-base-300">{esc_cat}</div></div>'
        return mark_safe(html)

    @admin.display(description=_("Status"))
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
        cls = conditional_escape(
            colors.get(
                obj.status, colors[UserSubscription.SubscriptionStatus.ACTIVE]
            )
        )
        icon = conditional_escape(icons.get(obj.status, "?"))
        label = conditional_escape(obj.get_status_display())
        html = f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium {cls} rounded-full gap-1"><span>{icon}</span><span>{label}</span></span>'
        return mark_safe(html)

    @admin.display(description=_("Dates"))
    def subscription_dates(self, obj):
        sub = conditional_escape(obj.subscribed_at.strftime("%Y-%m-%d %H:%M"))
        html = f'<div class="text-sm text-base-600 dark:text-base-400"><div>Subscribed: {sub}</div>'
        if obj.unsubscribed_at:
            unsub = conditional_escape(
                obj.unsubscribed_at.strftime("%Y-%m-%d %H:%M")
            )
            html += f"<div>Unsubscribed: {unsub}</div>"
        html += "</div>"
        return mark_safe(html)

    @action(
        description=str(_("Activate selected subscriptions")),
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def activate_subscriptions(self, request, queryset):
        # Bulk update is intentional here: no per-instance signals fire
        # on UserSubscription.save(), so .update() is safe and efficient.
        with transaction.atomic():
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
        description=str(_("Deactivate selected subscriptions")),
        variant=ActionVariant.WARNING,
        icon="cancel",
    )
    def deactivate_subscriptions(self, request, queryset):
        # Bulk update is intentional here: no per-instance signals fire
        # on UserSubscription.save(), so .update() is safe and efficient.
        with transaction.atomic():
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
