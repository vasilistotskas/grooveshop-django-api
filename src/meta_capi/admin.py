from django.contrib import admin

from admin.base import BaseModelAdmin
from admin.mixins import IsSuperuserOnlyModelAdmin
from meta_capi.models import MetaCapiEventLog


@admin.register(MetaCapiEventLog)
class MetaCapiEventLogAdmin(IsSuperuserOnlyModelAdmin, BaseModelAdmin):
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
    date_hierarchy = "created_at"
    list_per_page = 50
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
