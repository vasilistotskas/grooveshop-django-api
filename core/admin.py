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

admin.site.unregister(Setting)
admin.site.unregister(PeriodicTask)
admin.site.unregister(IntervalSchedule)
admin.site.unregister(CrontabSchedule)
admin.site.unregister(SolarSchedule)
admin.site.unregister(ClockedSchedule)

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


@admin.register(Setting)
class SettingAdmin(ModelAdmin):
    from core.forms.settings import SettingAdminForm

    form = SettingAdminForm
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = False
    list_filter_submit = True

    list_display = [
        "name_display",
        "value_type_badge",
        "value_preview",
        "description_preview",
    ]
    list_display_links = ["name_display"]
    list_filter = ["value_type"]
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


@admin.register(PeriodicTask)
class PeriodicTaskAdmin(BasePeriodicTaskAdmin, ModelAdmin):
    form = UnfoldPeriodicTaskForm


@admin.register(IntervalSchedule)
class IntervalScheduleAdmin(ModelAdmin):
    pass


@admin.register(CrontabSchedule)
class CrontabScheduleAdmin(BaseCrontabScheduleAdmin, ModelAdmin):
    pass


@admin.register(SolarSchedule)
class SolarScheduleAdmin(ModelAdmin):
    pass


@admin.register(ClockedSchedule)
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
