"""Read-only admin of the page builder.

The editor is the storefront's (``/_builder``), so the admin only lets a
store look at what is stored: the documents with their revisions, the
releases, and where a thing is used. Every write goes through
``builder.services``.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _
from unfold.decorators import action

from admin.base import BaseModelAdmin
from builder import scheduling
from builder.models import (
    BuilderDocument,
    BuilderReference,
    BuilderRelease,
    BuilderScheduledPublish,
    ScheduleStatus,
)
from builder.services import PUBLISH_PERMISSION


class ReadOnlyAdmin(BaseModelAdmin):
    def has_add_permission(self, request):  # type: ignore[override]
        return False

    def has_change_permission(self, request, obj=None):  # type: ignore[override]
        return False

    def has_delete_permission(self, request, obj=None):  # type: ignore[override]
        return False


@admin.register(BuilderDocument)
class BuilderDocumentAdmin(ReadOnlyAdmin):
    list_display = ("key", "kind", "draft_revision", "updated_at")
    list_filter = ("kind",)
    search_fields = ("key",)
    ordering = ("key",)
    readonly_fields = (
        "key",
        "kind",
        "draft",
        "draft_i18n",
        "draft_revision",
        "published_revision",
        "draft_catalogue_hash",
        "updated_by_staff",
        "created_at",
        "updated_at",
    )
    exclude = ("updated_by_staff_id", "is_deleted", "deleted_at")


@admin.register(BuilderRelease)
class BuilderReleaseAdmin(ReadOnlyAdmin):
    list_display = ("number", "published_at", "note")
    ordering = ("-number",)
    readonly_fields = (
        "number",
        "versions",
        "published_by_staff",
        "published_at",
        "note",
        "rolled_back_from",
    )
    exclude = ("published_by_staff_id",)


@admin.register(BuilderReference)
class BuilderReferenceAdmin(ReadOnlyAdmin):
    list_display = ("kind", "target", "document", "node_path")
    list_filter = ("kind",)
    search_fields = ("target", "document")


@admin.register(BuilderScheduledPublish)
class BuilderScheduledPublishAdmin(ReadOnlyAdmin):
    """Scheduled publishes, read only. The one thing to do here is to
    acknowledge a failure (the dashboard shows it until someone has)."""

    list_display = (
        "scheduled_for",
        "status",
        "executed_at",
        "acknowledged_at",
        "note",
    )
    list_filter = ("status",)
    ordering = ("-scheduled_for",)
    actions = ["acknowledge_failures"]
    readonly_fields = (
        "scheduled_for",
        "status",
        "versions",
        "revisions",
        "note",
        "created_by_staff",
        "executed_at",
        "release",
        "failure",
        "acknowledged_at",
        "acknowledged_by_staff",
    )
    exclude = ("created_by_staff_id", "acknowledged_by_staff_id")

    def has_acknowledge_permission(self, request) -> bool:
        return request.user.has_perm(PUBLISH_PERMISSION)

    @action(
        description=_("Acknowledge the failure"),
        permissions=["acknowledge"],
        icon="done",
    )
    def acknowledge_failures(self, request, queryset):
        done = 0
        for schedule in queryset.filter(status=ScheduleStatus.FAILED):
            scheduling.acknowledge(request.user, pk=schedule.pk)
            done += 1
        self.message_user(
            request,
            _("%(count)s failure(s) acknowledged.") % {"count": done},
            messages.SUCCESS,
        )
        return redirect(request.get_full_path())
