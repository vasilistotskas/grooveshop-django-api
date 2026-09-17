from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.admin import TabularInline

from admin.base import (
    BaseModelAdmin,
    BaseTranslatableAdmin,
    BaseTranslatableTabularInline,
)
from page_config.models import (
    ContentPage,
    ContentPageTranslation,
    NavigationColumn,
    NavigationLink,
    NavigationMenu,
    NavigationSlot,
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
        "i18n",
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
            _("SEO"),
            {
                "fields": ("seo_title", "seo_description", "seo_keywords"),
                "description": _(
                    "The storefront's <title> and meta description for "
                    "this page. Left empty, the page keeps its built-in "
                    "title and the store description."
                ),
            },
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
    """The chrome menu for one slot.

    ``items``/``i18n`` are the JSON this model used to store and are
    shown read-only: the menu is built from columns and links now, and
    an editable copy of the old blob would be a second source of truth
    that silently disagrees with what the storefront renders. They are
    dropped once every replica serves the relational menus.
    """

    compressed_fields = True
    warn_unsaved_form = True
    list_display = ("slot", "entry_count", "updated_at")
    fields = ("slot", "items", "i18n")
    readonly_fields = ("items", "i18n")

    def get_inlines(self, request, obj=None):
        # Only the footer groups its links under headings.
        if obj is not None and obj.slot == NavigationSlot.FOOTER:
            return [NavigationColumnInline]
        return [NavigationMenuLinkInline]

    @admin.display(description=_("Entries"))
    def entry_count(self, obj: NavigationMenu) -> int:
        if obj.slot == NavigationSlot.FOOTER:
            return obj.columns.count()
        return obj.links.count()


class NavigationLinkInline(BaseTranslatableTabularInline):
    """The links inside one footer column, or one flat menu.

    Four target fields rather than a typed path: the operator picks
    WHAT the link points at and the path is derived, so it follows a
    renamed slug and vanishes when the page is unpublished.

    ``label`` is left blank for a page link on purpose — the page's own
    translated title is used, so a bilingual store translates the
    document once instead of once per menu per locale.
    """

    model = NavigationLink
    extra = 0
    fields = (
        "content_page",
        "page_layout",
        "route",
        "url",
        "label",
        "sort_order",
    )
    autocomplete_fields = ("content_page", "page_layout")
    ordering_field = "sort_order"
    hide_ordering_field = True
    tab = True


@admin.register(NavigationColumn)
class NavigationColumnAdmin(BaseTranslatableAdmin):
    """One footer heading and its links.

    Django has no nested inlines (ticket #9025) and Unfold adds none,
    so the menu lists its columns and each column is edited here —
    the same drill-through ``RegionInline`` uses.
    """

    list_display = ("__str__", "menu", "link_count", "sort_order")
    list_filter = ("menu__slot",)
    fields = ("menu", "label", "icon", "sort_order")
    inlines = [NavigationLinkInline]

    @admin.display(description=_("Links"))
    def link_count(self, obj: NavigationColumn) -> int:
        return obj.links.count()


class NavigationColumnInline(BaseTranslatableTabularInline):
    """The footer's columns, in order.

    Links are edited one level down — follow "Change" on a row.
    """

    model = NavigationColumn
    extra = 0
    fields = ("label", "icon", "sort_order")
    ordering_field = "sort_order"
    hide_ordering_field = True
    show_change_link = True
    tab = True


class NavigationMenuLinkInline(NavigationLinkInline):
    """Header and mobile links, which have no columns to sit in."""

    fk_name = "menu"
    verbose_name = _("Link")
    verbose_name_plural = _("Links")


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
