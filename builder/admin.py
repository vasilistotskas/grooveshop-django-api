"""Read-only admin of the page builder.

The editor is the storefront's (``/_builder``), so the admin only lets a
store look at what is stored: the documents with their revisions, the
releases, and where a thing is used. Every write goes through
``builder.services``.
"""

from __future__ import annotations

from django.contrib import admin

from admin.base import BaseModelAdmin
from builder.models import BuilderDocument, BuilderReference, BuilderRelease


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
