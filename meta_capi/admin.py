from django.contrib import admin
from unfold.admin import ModelAdmin

from meta_capi.models import MetaCapiEventLog


@admin.register(MetaCapiEventLog)
class MetaCapiEventLogAdmin(ModelAdmin):
    list_display = (
        "event_name",
        "status",
        "events_received",
        "order",
        "user",
        "fbtrace_id",
        "created_at",
    )
    list_filter = ("status", "event_name", "created_at")
    search_fields = ("event_id", "fbtrace_id", "order__id", "user__email")
    readonly_fields = (
        "event_name",
        "event_id",
        "order",
        "user",
        "status",
        "fbtrace_id",
        "events_received",
        "error_message",
        "payload",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):  # type: ignore[override]
        return False

    def has_change_permission(self, request, obj=None):  # type: ignore[override]
        return False
