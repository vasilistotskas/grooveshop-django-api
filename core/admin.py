import logging

from django.contrib import admin
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop
from django_celery_beat.admin import (
    ClockedScheduleAdmin as BaseClockedScheduleAdmin,
)
from django_celery_beat.admin import (
    CrontabScheduleAdmin as BaseCrontabScheduleAdmin,
)
from django_celery_beat.admin import PeriodicTaskAdmin as BasePeriodicTaskAdmin
from django_celery_beat.admin import (
    PeriodicTaskForm,
    TaskChoiceField,
    TaskSelectWidget,
)
from django_celery_beat.models import (
    ClockedSchedule,
    CrontabSchedule,
    IntervalSchedule,
    PeriodicTask,
    SolarSchedule,
)
from django_celery_results.admin import (
    GroupResultAdmin as BaseGroupResultAdmin,
)
from django_celery_results.admin import TaskResultAdmin as BaseTaskResultAdmin
from django_celery_results.models import GroupResult, TaskResult
from django_tenants.utils import get_public_schema_name
from extra_settings.models import Setting
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import DropdownFilter
from unfold.decorators import display
from unfold.widgets import UnfoldAdminSelectWidget, UnfoldAdminTextInputWidget

from admin.base import BaseModelAdmin
from admin.mixins import (
    IsSuperuserOnlyModelAdmin,
    WithheldOnTenantHostModelAdmin,
)
from core.result_backend import ran_in_store, ran_on_platform, store_of

logger = logging.getLogger(__name__)


def _resync_platform_site(model, admin_class):
    """Mirror a LATE re-registration onto the control-plane site.

    ``admin.apps.MyAdminConfig.ready()`` copies the default registry
    onto ``platform_admin_site``, and the ``admin`` app precedes
    ``core`` in INSTALLED_APPS — so that copy happens BEFORE the
    overrides below replace the third-party classes. Without this
    re-sync the platform keeps the ORIGINAL ModelAdmin: production
    (2026-08-26) rendered extra_settings' own list_editable grid on
    the platform Settings changelist while the tenant admin rendered
    ours, and Scheduled Tasks / Task Results / Sites / User Sessions
    were left un-wrapped by Unfold for the same reason.

    Order-independent by design: if the platform copy has not run
    yet, the model is simply absent here and the copy picks up the
    already-overridden class.
    """
    from django.contrib.admin.exceptions import NotRegistered

    from admin.platform_site import platform_admin_site

    if model not in platform_admin_site._registry:
        return
    try:
        platform_admin_site.unregister(model)
    except NotRegistered:
        pass
    platform_admin_site.register(model, admin_class)


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
        (TaskResult, TaskResultAdmin),
        (GroupResult, GroupResultAdmin),
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
        _resync_platform_site(model_class, admin_class)

    for model, model_admin in dict(admin.site._registry).items():
        if model._meta.app_label not in [
            "djstripe",
            "knox",
            "socialaccount",
            "mfa",
            "account",
            "usersessions",
            "sites",
        ]:
            continue

        admin.site.unregister(model)

        # The mixin goes FIRST so its permission overrides win the
        # MRO: these are third-party admins (sites.Site, ...) that never
        # reach BaseModelAdmin.
        new_admin_class = type(
            f"{model.__name__}AdminOverride",
            (WithheldOnTenantHostModelAdmin, model_admin.__class__, ModelAdmin),
            {},
        )

        admin.site.register(model, new_admin_class)
        _resync_platform_site(model, new_admin_class)


class UnfoldTaskSelectWidget(UnfoldAdminSelectWidget, TaskSelectWidget):
    @cached_property
    def _modules(self):
        """Import every app's ``tasks`` module, so the list is complete.

        Directly, not through ``loader.import_default_modules()`` as the
        base widget does: that sends Celery's ``import_modules`` signal,
        on which the Django fixup runs every Django system check, on
        each render of this form. On the platform host dj-stripe's check
        queries ``djstripe_webhookendpoint``, a store-only table, swallows
        the DatabaseError and leaves the admin's transaction aborted, so
        the periodic-task form answered 500 there.
        """
        self.celery_app.autodiscover_tasks(force=True)


class UnfoldPeriodicTaskForm(PeriodicTaskForm):
    # Declared here rather than swapped in ``__init__``: the form's
    # field copy in ``BaseForm.__init__`` already builds the widget's
    # choices, and the base widget would import the task modules its
    # own way before the swap.
    regtask = TaskChoiceField(
        label=PeriodicTaskForm.base_fields["regtask"].label,
        required=False,
        widget=UnfoldTaskSelectWidget(),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["task"].widget = UnfoldAdminTextInputWidget()


# Ordered prefix → category map for the ~80 flat extra_settings rows.
# First match wins, so put the more specific prefixes before generic
# ones (MYDATA_GIFT_CARD_* is a myDATA knob, not a gift-card one).
# Names go through ``gettext_noop`` so makemessages extracts them: they
# stay plain English keys (the filter value) and are translated where
# displayed (``_(category)``).
# The "Other" bucket catches anything unmapped — extend this map when
# a new settings family lands.
SETTING_CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    (
        gettext_noop("Storefront UI"),
        (
            "MOBILE_BOTTOM_NAV_",
            "STICKY_ADD_TO_CART_",
            "RECENTLY_VIEWED_",
            "PRODUCT_ASSURANCES_",
            "CHAT_WIDGET_",
            "ACCOUNT_REVIEWS_",
            "PRODUCT_REVIEWS_",
            "BLOG_COMMENTS_",
            "FAVOURITES_",
            "NEWSLETTER_",
            "FEEDBACK_",
            "PRODUCT_ALERTS_",
            "PRICE_HISTORY_",
        ),
    ),
    (gettext_noop("Invoicing & myDATA"), ("INVOICE_", "MYDATA_", "B2B_")),
    (gettext_noop("Promotions & Gift Cards"), ("PROMOTIONS_", "GIFT_CARD")),
    (gettext_noop("Loyalty"), ("LOYALTY_",)),
    (
        gettext_noop("Shipping"),
        (
            "ACS_",
            "BOXNOW_",
            "CHECKOUT_SHIPPING_",
            "FREE_SHIPPING_",
            "DEFAULT_WEIGHT_",
        ),
    ),
    (
        gettext_noop("Orders & Checkout"),
        (
            "ORDER_",
            "PENDING_ORDER_",
            "STOCK_",
            "CART_",
            "CHECKOUT_",
            "ABANDONED_CART_",
            "OLD_GUEST_CART_",
        ),
    ),
    (
        gettext_noop("Emails & Engagement"),
        (
            "REENGAGEMENT_",
            "INACTIVE_USER_",
            "SUBSCRIPTION_",
            "CONTACT_",
        ),
    ),
    (gettext_noop("Operational Alerts"), ("OPS_ALERT_",)),
    (gettext_noop("Agent Commerce"), ("AGENT_", "PRODUCT_FEEDS_")),
    (gettext_noop("Analytics"), ("META_CAPI_",)),
    (gettext_noop("Search"), ("SEARCH_",)),
]


def setting_category(name: str) -> str:
    for category, prefixes in SETTING_CATEGORIES:
        if name.startswith(prefixes):
            return category
    return "Other"


class SettingCategoryFilter(DropdownFilter):
    """Group the flat settings list into functional areas.

    The categories come from the shared ``SETTING_CATEGORIES`` prefix
    map so the category column and the filter can never disagree.
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
        from django.db.models import Q

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


class SettingAdmin(BaseModelAdmin):
    from core.forms.settings import SettingAdminForm

    form = SettingAdminForm
    list_fullwidth = False
    ordering = ["name"]

    list_display = [
        "name_display",
        "category_label",
        "value_type_label",
        "value_preview",
        "description_preview",
    ]
    list_display_links = ["name_display"]
    list_filter = [SettingCategoryFilter, "value_type"]
    search_fields = ["name", "description"]

    class Media:
        css = {"all": ("extra_settings/css/extra_settings_admin.css",)}
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

    @display(description=_("Category"), label=True)
    def category_label(self, obj):
        return _(setting_category(obj.name))

    @display(description=_("Type"), label=True, ordering="value_type")
    def value_type_label(self, obj):
        return obj.get_value_type_display()

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


class PeriodicTaskAdmin(BasePeriodicTaskAdmin, BaseModelAdmin):
    form = UnfoldPeriodicTaskForm


class TaskResultStoreFilter(DropdownFilter):
    """Which store's task failed, from the schema ``TenantTask`` recorded.

    Task results live in one public-schema table, so this is a match on
    the stored ``meta`` (see ``core.result_backend``), not a foreign key.
    """

    title = _("Store")
    parameter_name = "store"

    #: Not a schema name: the public schema is not a store.
    PLATFORM = "platform"

    def lookups(self, request, model_admin):
        from tenant.models import Tenant

        return [
            (self.PLATFORM, _("Platform")),
            *(
                (schema_name, name)
                for schema_name, name in Tenant.objects.exclude(
                    schema_name=get_public_schema_name()
                )
                .order_by("name")
                .values_list("schema_name", "name")
            ),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        if value == self.PLATFORM:
            return queryset.filter(ran_on_platform())
        return queryset.filter(ran_in_store(value))


class TaskResultAdmin(BaseTaskResultAdmin, BaseModelAdmin):
    """Background-task failures for every store, on the control plane only.

    ``BaseModelAdmin``'s ``WithheldOnTenantHostModelAdmin`` keeps it off a
    store's own admin (``django_celery_results`` is a shared-only app): a
    store owner never sees another store's failures.
    """

    list_display = (
        "task_id",
        "store",
        "periodic_task_name",
        "task_name",
        "date_done",
        "status",
        "worker",
    )
    list_filter = (TaskResultStoreFilter, *BaseTaskResultAdmin.list_filter)

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context)
        changelist = (getattr(response, "context_data", None) or {}).get("cl")
        if changelist is not None:
            from tenant.models import Tenant

            # One query for the names, not one per row.
            names = dict(Tenant.objects.values_list("schema_name", "name"))
            for row in changelist.result_list:
                schema_name = store_of(row)
                row.store_label = (
                    _("Platform")
                    if schema_name in (None, get_public_schema_name())
                    else names.get(schema_name, schema_name)
                )
        return response

    @display(description=_("Store"))
    def store(self, obj):
        return getattr(obj, "store_label", "—")


class GroupResultAdmin(BaseGroupResultAdmin, BaseModelAdmin):
    pass


class IntervalScheduleAdmin(BaseModelAdmin):
    pass


class CrontabScheduleAdmin(BaseCrontabScheduleAdmin, BaseModelAdmin):
    pass


class SolarScheduleAdmin(BaseModelAdmin):
    pass


class ClockedScheduleAdmin(BaseClockedScheduleAdmin, BaseModelAdmin):
    pass


from core.cache.models import CachePurgeLog  # noqa: E402


@admin.register(CachePurgeLog)
class CachePurgeLogAdmin(IsSuperuserOnlyModelAdmin, BaseModelAdmin):
    # ``actor_email`` rather than ``actor``: this changelist is control-plane
    # only, where the cross-schema FK does resolve correctly, but showing the
    # stored string keeps one display that is right in every context and
    # survives the user row being deleted.
    list_display = (
        "created_at",
        "actor_email",
        "schema_name",
        "surface_summary",
        "total_django",
        "total_nuxt",
        "total_blocked",
        "dry_run",
    )
    date_hierarchy = "created_at"
    list_filter = ("dry_run", "schema_name", "created_at")
    search_fields = ("actor_email", "schema_name")
    readonly_fields = (
        "actor",
        "actor_email",
        "schema_name",
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
