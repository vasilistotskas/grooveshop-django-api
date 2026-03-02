from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, TabularInline

from page_config.models import PageLayout, PageSection


class PageSectionInline(TabularInline):
    model = PageSection
    extra = 0
    fields = (
        "component_type",
        "title",
        "is_visible",
        "props",
        "sort_order",
    )
    readonly_fields = ("sort_order",)
    ordering = ("sort_order",)


@admin.register(PageLayout)
class PageLayoutAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True

    list_display = (
        "page_type",
        "title",
        "is_published",
        "updated_at",
    )
    list_filter = ("is_published", "page_type")
    list_editable = ("is_published",)
    search_fields = ("page_type", "title")
    readonly_fields = ("id", "uuid", "created_at", "updated_at")

    fieldsets = (
        (
            _("Page"),
            {"fields": ("page_type", "title")},
        ),
        (
            _("Publishing"),
            {"fields": ("is_published",)},
        ),
        (
            _("Metadata"),
            {
                "fields": ("metadata",),
                "classes": ("collapse",),
            },
        ),
        (
            _("System"),
            {
                "fields": (
                    "id",
                    "uuid",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    inlines = [PageSectionInline]
