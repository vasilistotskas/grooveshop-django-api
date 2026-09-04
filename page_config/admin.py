from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.admin import TabularInline

from admin.base import BaseModelAdmin, BaseTranslatableAdmin
from page_config.models import (
    ContentPage,
    ContentPageTranslation,
    NavigationMenu,
    PageLayout,
    PageSection,
)


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
class PageLayoutAdmin(BaseModelAdmin):
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


@admin.register(NavigationMenu)
class NavigationMenuAdmin(BaseModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_display = ("slot", "updated_at")
    fields = ("slot", "items")

    def save_model(self, request, obj, form, change):
        # Field-level JSON validation mirrors the storefront contract.
        from page_config.schemas import (
            validate_navigation_items,
        )

        validate_navigation_items(obj.slot, obj.items)
        super().save_model(request, obj, form, change)


class ContentPageTranslationInline(TabularInline):
    model = ContentPageTranslation
    extra = 0
    fields = ("language_code", "title")
    show_change_link = True

    tab = True


@admin.register(ContentPage)
class ContentPageAdmin(BaseTranslatableAdmin):
    list_display = (
        "title_display",
        "slug",
        "is_published",
        "updated_at",
    )
    list_filter = ("is_published",)
    list_editable = ("is_published",)
    search_fields = ("translations__title", "slug")
    readonly_fields = ("id", "uuid", "created_at", "updated_at")

    fieldsets = (
        (
            _("Content"),
            {"fields": ("title", "body"), "classes": ("wide",)},
        ),
        (
            _("Organization"),
            {"fields": ("slug", "is_published"), "classes": ("wide",)},
        ),
        (
            _("SEO"),
            {
                "fields": ("seo_title", "seo_description", "seo_keywords"),
                "classes": ("collapse",),
            },
        ),
        (
            _("System"),
            {
                "fields": ("id", "uuid", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    inlines = [ContentPageTranslationInline]

    @admin.display(description=_("Title"), ordering="translations__title")
    def title_display(self, obj):
        return obj.safe_translation_getter("title", any_language=True) or (
            obj.slug
        )
