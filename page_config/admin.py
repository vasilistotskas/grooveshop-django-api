from django.conf import settings
from django.contrib import admin, messages
from django.db.models import Count
from django.utils.html import escape, format_html_join
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from unfold.admin import TabularInline
from unfold.decorators import action

from admin.base import (
    BaseModelAdmin,
    BaseTranslatableAdmin,
    BaseTranslatableTabularInline,
)
from page_config.defaults import (
    pending_legal_reviews,
    tenant_document_context,
)
from page_config.legal_documents import (
    LEGAL_TEXT_REVISION,
    LEGAL_TEXT_UPDATES,
    pending_legal_updates,
    render_legal_fragment,
)
from page_config.models import (
    ContentPage,
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
    tab = True
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
class PageLayoutAdmin(BaseTranslatableAdmin):
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

    The row is only the slot; everything a visitor sees is the columns
    and links edited through the inlines below.
    """

    list_display = ("slot", "entry_count", "updated_at")
    fields = ("slot",)

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

    list_select_related = ("menu",)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .prefetch_related("translations")
            .annotate(links_total=Count("links"))
        )

    @admin.display(description=_("Links"), ordering="links_total")
    def link_count(self, obj: NavigationColumn) -> int:
        return obj.links_total


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
    readonly_fields = (
        "id",
        "uuid",
        "created_at",
        "updated_at",
        "legal_update_text",
    )
    actions = ["mark_legal_update_reviewed"]

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

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("translations")

    @admin.display(description=_("Title"), ordering="translations__title")
    def title_display(self, obj):
        return obj.safe_translation_getter("title", any_language=True) or (
            obj.slug
        )

    # --- Platform legal-text updates -----------------------------------
    #
    # A legal page the platform could not update itself (the text is the
    # merchant's) is flagged here until the merchant adds the new text
    # and marks it reviewed. See ``LegalTextUpdate``.

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if obj is None or not pending_legal_updates(
            obj.slug, obj.legal_text_revision
        ):
            return fieldsets
        return (
            (
                _("Platform update to review"),
                {
                    "fields": ("legal_update_text",),
                    "classes": ("wide",),
                    "description": LEGAL_UPDATE_HELP,
                },
            ),
            *fieldsets,
        )

    @admin.display(description=_("Text to add"))
    def legal_update_text(self, obj):
        updates = pending_legal_updates(obj.slug, obj.legal_text_revision)
        if not updates:
            return "-"
        site_host, store_name = tenant_document_context()
        return format_html_join(
            "",
            '<div class="mb-6"><p class="font-semibold mb-2">{}</p>'
            '<div class="prose dark:prose-invert max-w-none">{}</div></div>',
            (
                (
                    dict(settings.LANGUAGES).get(language, language),
                    # Platform-authored HTML, trusted; the two tenant
                    # values going into it are escaped.
                    mark_safe(
                        render_legal_fragment(
                            html,
                            site_host=escape(site_host),
                            store_name=escape(store_name),
                        )
                    ),
                )
                for update in updates
                for language, html in update.sections.items()
            ),
        )

    def changelist_view(self, request, extra_context=None):
        pending = pending_legal_reviews()
        if pending and request.method == "GET":
            titles = sorted({str(page) for page, _update in pending})
            self.message_user(
                request,
                f"{LEGAL_UPDATE_NOTICE} {', '.join(titles)}",
                messages.WARNING,
            )
        return super().changelist_view(request, extra_context)

    @action(
        description=_("Mark platform legal update as reviewed"),
        permissions=["change"],
        icon="task_alt",
    )
    def mark_legal_update_reviewed(self, request, queryset):
        count = queryset.filter(
            slug__in={update.slug for update in LEGAL_TEXT_UPDATES}
        ).update(legal_text_revision=LEGAL_TEXT_REVISION)
        self.message_user(
            request,
            _("%(count)d legal page(s) marked as reviewed.") % {"count": count},
            messages.SUCCESS,
        )


LEGAL_UPDATE_NOTICE = _(
    "The platform has updated its legal text, and these pages were not "
    "changed automatically because their text is yours:"
)

LEGAL_UPDATE_HELP = _(
    "The platform added the text below to its own version of this "
    "document. Your page was not changed automatically, because its text "
    "(or a translation of it) is yours. Add the text to every language "
    "your store serves, adjusting it if your store works differently, "
    'then select this page in the list and run "Mark platform legal '
    'update as reviewed".'
)
