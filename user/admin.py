from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html_join
from django.utils.translation import gettext_lazy as _
from unfold.admin import TabularInline

from admin.base import BaseModelAdmin, BaseTranslatableAdmin
from admin.displays import (
    choice_label,
    format_dt,
    header_two_line,
    relative_time,
)
from admin.export import ExportActionMixin
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateFilter,
    RangeDateTimeFilter,
    RangeNumericListFilter,
    RelatedDropdownFilter,
)
from unfold.decorators import action, display
from unfold.enums import ActionVariant
from unfold.forms import (
    AdminPasswordChangeForm,
    UserChangeForm,
    UserCreationForm,
)

from admin.mixins import IsSuperuserOnlyModelAdmin
from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction
from loyalty.services import LoyaltyService
from user.models import UserAccount
from user.models.address import UserAddress
from user.models.data_export import UserDataExport
from user.models.subscription import SubscriptionTopic, UserSubscription

admin.site.unregister(Group)

# ── Local (single-app) TextChoices variant maps ────────────────────────

SUBSCRIPTION_TOPIC_CATEGORY_VARIANT: dict[str, str] = {
    SubscriptionTopic.TopicCategory.MARKETING: "warning",
    SubscriptionTopic.TopicCategory.PRODUCT: "info",
    SubscriptionTopic.TopicCategory.ACCOUNT: "success",
    SubscriptionTopic.TopicCategory.SYSTEM: "danger",
    SubscriptionTopic.TopicCategory.NEWSLETTER: "primary",
    SubscriptionTopic.TopicCategory.PROMOTIONAL: "warning",
    SubscriptionTopic.TopicCategory.OTHER: "default",
}

USER_SUBSCRIPTION_STATUS_VARIANT: dict[str, str] = {
    UserSubscription.SubscriptionStatus.ACTIVE: "success",
    UserSubscription.SubscriptionStatus.PENDING: "warning",
    UserSubscription.SubscriptionStatus.UNSUBSCRIBED: "danger",
    UserSubscription.SubscriptionStatus.BOUNCED: "primary",
}


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
    per_page = 15
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
    per_page = 15
    fields = ("topic", "status", "subscribed_at", "unsubscribed_at")
    readonly_fields = ("subscribed_at", "unsubscribed_at")
    show_change_link = True
    tab = True


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, BaseModelAdmin):
    pass


@admin.register(UserAccount)
class UserAdmin(ExportActionMixin, BaseModelAdmin):
    actions = ["export_csv", "export_xml"]

    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    list_display = [
        "identity",
        "contact_info_display",
        "location_display",
        "is_active",
        "is_staff",
        "is_superuser",
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
    search_help_text = _(
        "Search by email, username, name, phone, city, address, or bio."
    )

    list_select_related = ["country", "region"]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "engagement_metrics",
        "social_links_summary",
        "loyalty_points_balance",
        "loyalty_total_xp",
        "loyalty_level",
        "loyalty_tier_name",
    ]

    ordering = ["-created_at"]
    date_hierarchy = "created_at"

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
                "fields": ("engagement_metrics",),
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

    def get_form(self, request, obj=None, change=False, **kwargs):
        # Unfold's ``ModelAdmin.get_fieldsets`` swaps in ``add_fieldsets``
        # (password1/password2) when adding, but the base ``get_form``
        # never swaps ``self.form`` for ``self.add_form`` — so the add
        # view built its ModelForm off ``UserChangeForm``
        # (``Meta.fields = "__all__"``) while the computed ``fields``
        # list already included password1/password2, which aren't
        # model fields -> ``FieldError``. Mirrors
        # ``django.contrib.auth.admin.UserAdmin.get_form``.
        if obj is None:
            kwargs.setdefault("form", self.add_form)
        return super().get_form(request, obj, change=change, **kwargs)

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

    @display(description=_("User"), header=True, ordering="last_name")
    def identity(self, obj):
        image_path = obj.image.url if obj.image else None
        return header_two_line(
            obj.full_name or obj.username or str(_("Anonymous")),
            obj.email,
            image_path=image_path,
        )

    @display(description=_("Contact"))
    def contact_info_display(self, obj):
        parts = [str(p) for p in (obj.phone, obj.username) if p]
        return " · ".join(parts) if parts else _("No contact info")

    @display(description=_("Location"))
    def location_display(self, obj):
        parts = [
            p for p in (obj.city, str(obj.country) if obj.country else "") if p
        ]
        return ", ".join(parts) if parts else _("No location")

    @display(description=_("Social"))
    def social_links_display(self, obj):
        platforms = [
            name
            for field, name in (
                ("website", _("Website")),
                ("linkedin", "LinkedIn"),
                ("github", "GitHub"),
                ("twitter", "Twitter"),
            )
            if getattr(obj, field)
        ]
        return ", ".join(str(p) for p in platforms) if platforms else "—"

    @display(description=_("Engagement"))
    def engagement_metrics(self, obj):
        return _("%(active)d/%(subs)d subscriptions · %(addrs)d addresses") % {
            "active": getattr(obj, "active_subscription_count", 0),
            "subs": getattr(obj, "subscription_count", 0),
            "addrs": getattr(obj, "address_count", 0),
        }

    @display(description=_("Last Activity"), ordering="updated_at")
    def last_activity(self, obj):
        return f"{format_dt(obj.updated_at)} ({relative_time(obj.updated_at)})"

    @display(description=_("Social Links Summary"))
    def social_links_summary(self, obj):
        fields = {
            "website": "Website",
            "linkedin": "LinkedIn",
            "github": "GitHub",
            "twitter": "Twitter",
            "facebook": "Facebook",
            "instagram": "Instagram",
            "youtube": "YouTube",
        }
        links = [
            (getattr(obj, field), name)
            for field, name in fields.items()
            if getattr(obj, field)
        ]
        if not links:
            return _("No social media links")
        return format_html_join(
            ", ",
            '<a href="{}" target="_blank" rel="noopener">{}</a>',
            links,
        )

    @display(description=_("Points Balance"))
    def loyalty_points_balance(self, obj):
        return _("%(balance)d points") % {
            "balance": LoyaltyService.get_user_balance(obj)
        }

    @display(description=_("Total XP"))
    def loyalty_total_xp(self, obj):
        return _("%(xp)d XP") % {"xp": obj.total_xp}

    @display(description=_("Level"))
    def loyalty_level(self, obj):
        return _("Level %(level)d") % {
            "level": LoyaltyService.get_user_level(obj)
        }

    @display(description=_("Tier"))
    def loyalty_tier_name(self, obj):
        tier = LoyaltyService.get_user_tier(obj)
        return str(tier) if tier else _("No tier")

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
class UserAddressAdmin(BaseModelAdmin):
    list_display = [
        "address_display",
        "contact_person",
        "location_info",
        "is_main",
        "phone",
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

    @display(description=_("Address"))
    def address_display(self, obj):
        return f"{obj.title} — {obj.street} {obj.street_number}, {obj.city}"

    @display(description=_("Contact Person"))
    def contact_person(self, obj):
        return f"{obj.first_name} {obj.last_name} ({obj.user.email})"

    @display(description=_("Location"))
    def location_info(self, obj):
        parts = [obj.city]
        if obj.country:
            parts.append(str(obj.country))
        if obj.zipcode:
            parts.append(obj.zipcode)
        return ", ".join(parts)


@admin.register(SubscriptionTopic)
class SubscriptionTopicAdmin(BaseTranslatableAdmin):
    # Override: this admin's list is intentionally narrow (fixed-width
    # for scanning of the small ~10-row topic catalogue).
    list_fullwidth = False

    list_display = [
        "name_display",
        "category_label",
        "is_active",
        "is_default",
        "requires_confirmation",
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
    # ``get_queryset`` annotates ``Count`` aggregates, which otherwise
    # strips the model's default ``Meta.ordering`` from the query.
    ordering = ("-created_at",)

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

    category_label = choice_label(
        "category",
        variants=SUBSCRIPTION_TOPIC_CATEGORY_VARIANT,
        description=_("Category"),
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

    @display(description=_("Topic"), ordering="slug")
    def name_display(self, obj):
        name = obj.safe_translation_getter("name", any_language=True) or _(
            "Unnamed Topic"
        )
        return f"{name} ({obj.slug})"

    @display(description=_("Subscribers"))
    def subscriber_metrics(self, obj):
        return _("%(active)d/%(total)d active") % {
            "active": getattr(obj, "active_subscribers", 0),
            "total": getattr(obj, "total_subscribers", 0),
        }


@admin.register(UserSubscription)
class UserSubscriptionAdmin(BaseModelAdmin):
    list_display = [
        "subscription_info",
        "user_info",
        "topic_info",
        "status_label",
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

    status_label = choice_label(
        "status",
        variants=USER_SUBSCRIPTION_STATUS_VARIANT,
        description=_("Status"),
    )

    @display(description=_("Subscription"), ordering="created_at")
    def subscription_info(self, obj):
        return f"#{obj.id} — {format_dt(obj.created_at, fmt='%d/%m/%Y')}"

    @display(description=_("User"))
    def user_info(self, obj):
        name = obj.user.full_name or obj.user.username or _("Anonymous")
        return f"{name} ({obj.user.email})"

    @display(description=_("Topic"))
    def topic_info(self, obj):
        name = obj.topic.safe_translation_getter(
            "name", any_language=True
        ) or _("Unnamed Topic")
        return f"{name} ({obj.topic.get_category_display()})"

    @display(description=_("Dates"))
    def subscription_dates(self, obj):
        if obj.unsubscribed_at:
            return _("Subscribed %(sub)s · Unsubscribed %(unsub)s") % {
                "sub": format_dt(obj.subscribed_at),
                "unsub": format_dt(obj.unsubscribed_at),
            }
        return _("Subscribed %(sub)s") % {"sub": format_dt(obj.subscribed_at)}

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


@admin.register(UserDataExport)
class UserDataExportAdmin(IsSuperuserOnlyModelAdmin, BaseModelAdmin):
    """Read-only ledger of GDPR data-export requests."""

    list_display = (
        "user",
        "status",
        "file_size",
        "expires_at",
        "created_at",
    )
    list_filter = (
        "status",
        ("created_at", RangeDateTimeFilter),
        ("expires_at", RangeDateTimeFilter),
    )
    search_fields = ("user__email", "user__username", "token")
    readonly_fields = (
        "user",
        "status",
        "file_path",
        "file_size",
        "token",
        "expires_at",
        "error_message",
        "created_at",
        "updated_at",
        "uuid",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False
