import logging

from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from django_celery_beat.admin import (
    ClockedScheduleAdmin as BaseClockedScheduleAdmin,
)
from django_celery_beat.admin import (
    CrontabScheduleAdmin as BaseCrontabScheduleAdmin,
)
from django_celery_beat.admin import PeriodicTaskAdmin as BasePeriodicTaskAdmin
from django_celery_beat.admin import PeriodicTaskForm, TaskSelectWidget
from django_celery_beat.models import (
    ClockedSchedule,
    CrontabSchedule,
    IntervalSchedule,
    PeriodicTask,
    SolarSchedule,
)
from extra_settings.models import Setting
from unfold.admin import ModelAdmin
from unfold.widgets import UnfoldAdminSelectWidget, UnfoldAdminTextInputWidget

from admin.mixins import IsSuperuserOnlyModelAdmin

logger = logging.getLogger(__name__)


def override_third_party_admins():
    """Re-register third-party admin classes with Unfold ModelAdmin.

    Must be called from CoreConfig.ready(), not at module level,
    because SHARED_APPS/TENANT_APPS ordering means third-party
    admin modules may not have been imported yet during autodiscover.
    """
    from django.contrib.admin.exceptions import NotRegistered

    for model_class, admin_class in [
        (Setting, SettingAdmin),
        (PeriodicTask, PeriodicTaskAdmin),
        (IntervalSchedule, IntervalScheduleAdmin),
        (CrontabSchedule, CrontabScheduleAdmin),
        (SolarSchedule, SolarScheduleAdmin),
        (ClockedSchedule, ClockedScheduleAdmin),
    ]:
        try:
            admin.site.unregister(model_class)
        except NotRegistered:
            pass
        admin.site.register(model_class, admin_class)

    for model, model_admin in dict(admin.site._registry).items():
        if model._meta.app_label not in [
            "djstripe",
            "knox",
            "socialaccount",
            "mfa",
            # Linked from the System sidebar but previously rendered with
            # default Django widgets — wrap so every admin form is unfold.
            "django_celery_results",
            "account",
            "usersessions",
            "sites",
        ]:
            continue

        admin.site.unregister(model)

        new_admin_class = type(
            f"{model.__name__}AdminOverride",
            (model_admin.__class__, ModelAdmin),
            {},
        )

        admin.site.register(model, new_admin_class)


class UnfoldTaskSelectWidget(UnfoldAdminSelectWidget, TaskSelectWidget):
    pass


class UnfoldPeriodicTaskForm(PeriodicTaskForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["task"].widget = UnfoldAdminTextInputWidget()
        self.fields["regtask"].widget = UnfoldTaskSelectWidget()


# Ordered prefix → category map for the ~80 flat extra_settings rows.
# First match wins, so put the more specific prefixes before generic
# ones (MYDATA_GIFT_CARD_* is a myDATA knob, not a gift-card one).
# The "Other" bucket catches anything unmapped — extend this map when
# a new settings family lands.
SETTING_CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    (
        "Storefront UI",
        (
            "MOBILE_BOTTOM_NAV_",
            "STICKY_ADD_TO_CART_",
            "RECENTLY_VIEWED_",
            "CHAT_WIDGET_",
            "ACCOUNT_REVIEWS_",
            "PRODUCT_REVIEWS_",
            "BLOG_COMMENTS_",
            "FAVOURITES_",
            "NEWSLETTER_",
            "FEEDBACK_",
            "PRODUCT_ALERTS_",
        ),
    ),
    ("Invoicing & myDATA", ("INVOICE_", "MYDATA_", "B2B_")),
    ("Promotions & Gift Cards", ("PROMOTIONS_", "GIFT_CARD")),
    ("Loyalty", ("LOYALTY_",)),
    (
        "Shipping",
        (
            "ACS_",
            "BOXNOW_",
            "CHECKOUT_SHIPPING_",
            "FREE_SHIPPING_",
            "DEFAULT_WEIGHT_",
        ),
    ),
    (
        "Orders & Checkout",
        (
            "ORDER_",
            "PENDING_ORDER_",
            "STOCK_",
            "CART_",
            "CHECKOUT_",
            "LOW_STOCK_",
            "ABANDONED_CART_",
            "OLD_GUEST_CART_",
        ),
    ),
    (
        "Emails & Engagement",
        (
            "REENGAGEMENT_",
            "INACTIVE_USER_",
            "NOTIFICATION_",
            "SUBSCRIPTION_",
            "CONTACT_EMAIL",
        ),
    ),
    ("Agent Commerce", ("AGENT_", "PRODUCT_FEEDS_")),
    ("Analytics", ("META_CAPI_",)),
    ("Search", ("SEARCH_",)),
]


def setting_category(name: str) -> str:
    for category, prefixes in SETTING_CATEGORIES:
        if name.startswith(prefixes):
            return category
    return "Other"


class SettingCategoryFilter(admin.SimpleListFilter):
    """Group the flat settings list into functional areas.

    Rendered as an unfold dropdown via the admin's
    ``list_filter``; the categories come from the shared
    ``SETTING_CATEGORIES`` prefix map so the badge column and the
    filter can never disagree.
    """

    title = _("Category")
    parameter_name = "category"

    def lookups(self, request, model_admin):
        return [
            *((category, _(category)) for category, _p in SETTING_CATEGORIES),
            ("Other", _("Other")),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        from django.db.models import Q  # noqa: PLC0415

        prefix_map = dict(SETTING_CATEGORIES)
        if value == "Other":
            # Everything NO category prefix matches.
            query = Q()
            for prefixes in prefix_map.values():
                for prefix in prefixes:
                    query |= Q(name__startswith=prefix)
            return queryset.exclude(query)
        prefixes = prefix_map.get(value)
        if not prefixes:
            return queryset
        query = Q()
        for prefix in prefixes:
            query |= Q(name__startswith=prefix)
        return queryset.filter(query)


class SettingAdmin(ModelAdmin):
    from core.forms.settings import SettingAdminForm

    form = SettingAdminForm
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = False
    list_filter_submit = True
    ordering = ["name"]

    list_display = [
        "name_display",
        "category_badge",
        "value_type_badge",
        "value_preview",
        "description_preview",
    ]
    list_display_links = ["name_display"]
    list_filter = [SettingCategoryFilter, "value_type"]
    search_fields = ["name", "description"]

    class Media:
        css = {
            "all": (
                "extra_settings/css/setting_badges.css",
                "extra_settings/css/extra_settings_admin.css",
            )
        }
        js = ("extra_settings/js/extra_settings_admin.js",)

    # Use fieldsets for better control over field rendering
    def get_fieldsets(self, request, obj=None):
        """Dynamically return fieldsets based on the setting type."""
        base_fieldset = (
            _("Setting Information"),
            {
                "fields": ("name", "value_type", "description"),
            },
        )

        # Always show all value fields - JavaScript will handle visibility
        value_fieldset = (
            _("Value"),
            {
                "fields": (
                    "value_bool",
                    "value_int",
                    "value_float",
                    "value_decimal",
                    "value_string",
                    "value_text",
                    "value_json",
                    "value_date",
                    "value_datetime",
                    "value_time",
                    "value_duration",
                    "value_email",
                    "value_url",
                    "value_file",
                    "value_image",
                ),
                "description": _(
                    "Enter the value based on the selected type above"
                ),
            },
        )

        validator_fieldset = (
            _("Validation"),
            {
                "fields": ("validator",),
                "classes": ("collapse",),
            },
        )

        return [base_fieldset, value_fieldset, validator_fieldset]

    @admin.display(description=_("Name"), ordering="name")
    def name_display(self, obj):
        return obj.name

    @admin.display(description=_("Category"))
    def category_badge(self, obj):
        from django.utils.html import format_html  # noqa: PLC0415

        return format_html(
            '<span class="setting-type-badge" data-type="{category}">'
            "{category}</span>",
            category=setting_category(obj.name),
        )

    @admin.display(description=_("Type"))
    def value_type_badge(self, obj):
        from django.utils.html import format_html  # noqa: PLC0415

        return format_html(
            '<span class="setting-type-badge" data-type="{type}">{type}</span>',
            type=obj.value_type,
        )

    @admin.display(description=_("Current Value"))
    def value_preview(self, obj):
        try:
            value = str(obj.value)
        except Exception:
            return "—"
        return value[:50] + "…" if len(value) > 50 else value

    @admin.display(description=_("Description"))
    def description_preview(self, obj):
        if not obj.description:
            return "—"
        desc = obj.description
        return desc[:60] + "…" if len(desc) > 60 else desc


class PeriodicTaskAdmin(BasePeriodicTaskAdmin, ModelAdmin):
    form = UnfoldPeriodicTaskForm


class IntervalScheduleAdmin(ModelAdmin):
    pass


class CrontabScheduleAdmin(BaseCrontabScheduleAdmin, ModelAdmin):
    pass


class SolarScheduleAdmin(ModelAdmin):
    pass


class ClockedScheduleAdmin(BaseClockedScheduleAdmin, ModelAdmin):
    pass


from core.cache.models import CachePurgeLog  # noqa: E402


@admin.register(CachePurgeLog)
class CachePurgeLogAdmin(IsSuperuserOnlyModelAdmin, ModelAdmin):
    list_display = (
        "created_at",
        "actor",
        "surface_summary",
        "total_django",
        "total_nuxt",
        "total_blocked",
        "dry_run",
    )
    list_filter = ("dry_run", "created_at")
    search_fields = ("actor__email", "actor__username")
    readonly_fields = (
        "actor",
        "created_at",
        "surfaces",
        "dry_run",
        "total_django",
        "total_nuxt",
        "total_blocked",
        "detail",
    )

    @admin.display(description="Surfaces")
    def surface_summary(self, obj):
        codes = obj.surfaces or []
        return ", ".join(codes) if codes else "—"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
