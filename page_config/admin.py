from django.conf import settings
from django.contrib import admin, messages
from django.utils.html import escape, format_html_join
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from django.utils.translation import npgettext_lazy
from unfold.decorators import action

from admin.base import (
    BaseModelAdmin,
    BaseTranslatableAdmin,
)
from core.store_settings import store_settings
from core.utils.sanitize import sanitize_html
from page_config.content_pages import (
    pending_legal_reviews,
    tenant_document_context,
)
from page_config.legal_documents import (
    LEGAL_DOCUMENT_SLUGS,
    LEGAL_TEXT_REVISION,
    LEGAL_TEXT_UPDATES,
    RETURN_POLICY_SLUG,
    pending_legal_updates,
    render_legal_fragment,
)
from page_config.legal_guard import (
    NOT_DELETABLE,
    is_legal_page,
    translation_deletable,
)
from page_config.models import (
    ContentPage,
    ContentPageTranslation,
    LegalDocumentVersion,
)


@admin.register(LegalDocumentVersion)
class LegalDocumentVersionAdmin(BaseModelAdmin):
    """The legal texts orders were placed under, exactly as accepted.

    Read-only: a version is evidence. It is written when an order
    accepts a text (``LegalDocumentVersion.objects.in_force``) and
    never changed or deleted here.
    """

    prefetch_translations = ("page__translations",)
    list_display = ("page", "language_code", "short_hash", "created_at")
    list_filter = ("page", "language_code")
    search_fields = ("content_hash", "title")
    readonly_fields = (
        "page",
        "language_code",
        "title",
        "body_display",
        "content_hash",
        "created_at",
    )
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Hash"))
    def short_hash(self, obj):
        return obj.content_hash[:12]

    @admin.display(description=_("Body"))
    def body_display(self, obj):
        # Sanitised again on the way out: the stored text is evidence,
        # kept as it was accepted, and is shown, never trusted.
        return mark_safe(sanitize_html(obj.body))


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
            {"fields": ("title", "body")},
        ),
        (
            _("Organization"),
            {"fields": ("slug", "is_published")},
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

    def has_delete_permission(self, request, obj=None):
        # A legal page, and its translation in a language the store
        # serves, are never deleted (page_config.legal_guard). Parler's
        # delete-translation view asks with the translation.
        if isinstance(obj, ContentPage) and is_legal_page(obj.slug):
            return False
        if isinstance(obj, ContentPageTranslation) and not (
            translation_deletable(obj.master.slug, obj.language_code)
        ):
            return False
        return super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        # The bulk action asks no per-object permission: skip the legal
        # pages and say so.
        legal = queryset.filter(slug__in=LEGAL_DOCUMENT_SLUGS)
        if legal.exists():
            self.message_user(request, NOT_DELETABLE, messages.ERROR)
        super().delete_queryset(
            request, queryset.exclude(slug__in=LEGAL_DOCUMENT_SLUGS)
        )

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

    # --- Return terms ---------------------------------------------------
    #
    # The storefront gives Google the return window and who pays from the
    # store settings, not from this page's text, so a save of the return
    # policy says what Google is told and where to change it.

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.slug != RETURN_POLICY_SLUG:
            return
        terms = store_settings(("RETURN_WINDOW_DAYS", "RETURN_FEES"))
        days = terms["RETURN_WINDOW_DAYS"]
        fees = terms["RETURN_FEES"]
        self.message_user(
            request,
            RETURN_TERMS_REMINDER
            % {
                "window": (
                    RETURN_WINDOW % {"days": days} if days else NOT_STATED
                ),
                "fees": RETURN_FEES_LABELS.get(fees, NOT_STATED),
            },
            messages.INFO,
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


RETURN_TERMS_REMINDER = _(
    "Google is told your return terms from Store Settings, not from this "
    "page: return window %(window)s (RETURN_WINDOW_DAYS), return "
    "shipping %(fees)s (RETURN_FEES). If you changed either here, change "
    "the setting too."
)

NOT_STATED = _("not stated")

RETURN_WINDOW = npgettext_lazy(
    "return window", "%(days)s day", "%(days)s days", "days"
)

RETURN_FEES_LABELS = {
    "free": _("paid by the store"),
    "customer": _("paid by the customer"),
}

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
