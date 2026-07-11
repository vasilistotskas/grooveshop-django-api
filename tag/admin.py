from datetime import timedelta

from django.contrib import admin, messages
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Count
from django.utils import timezone
from django.utils.translation import gettext_lazy as _, ngettext
from unfold.admin import GenericTabularInline
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)
from unfold.decorators import action
from unfold.enums import ActionVariant

from admin.base import BaseModelAdmin, BaseTranslatableAdmin
from admin.displays import format_dt

from tag.models.tag import Tag
from tag.models.tagged_item import TaggedItem


class TagStatusFilter(DropdownFilter):
    title = _("Tag Status")
    parameter_name = "tag_status"

    def lookups(self, request, model_admin):
        return [
            ("active", _("Active Tags")),
            ("inactive", _("Inactive Tags")),
            ("used", _("Used Tags")),
            ("unused", _("Unused Tags")),
            ("popular", _("Popular (>10 uses)")),
            ("recent", _("Recently Created")),
        ]

    def queryset(self, request, queryset):
        match self.value():
            case "active":
                return queryset.filter(active=True)
            case "inactive":
                return queryset.filter(active=False)
            case "used":
                return queryset.filter(taggeditem__isnull=False).distinct()
            case "unused":
                return queryset.filter(taggeditem__isnull=True)
            case "popular":
                return queryset.annotate(
                    usage_count=models.Count("taggeditem")
                ).filter(usage_count__gt=10)
            case "recent":
                cutoff = timezone.now() - timedelta(days=7)
                return queryset.filter(created_at__gte=cutoff)
            case _:
                return queryset


class TagUsageFilter(DropdownFilter):
    title = _("Usage Range")
    parameter_name = "usage_range"

    def lookups(self, request, model_admin):
        return [
            ("single", _("Single Use (1)")),
            ("low", _("Low Usage (2-5)")),
            ("medium", _("Medium Usage (6-20)")),
            ("high", _("High Usage (21-50)")),
            ("very_high", _("Very High Usage (50+)")),
        ]

    def queryset(self, request, queryset):
        qs = queryset.annotate(usage_count=models.Count("taggeditem"))
        match self.value():
            case "single":
                return qs.filter(usage_count=1)
            case "low":
                return qs.filter(usage_count__range=(2, 5))
            case "medium":
                return qs.filter(usage_count__range=(6, 20))
            case "high":
                return qs.filter(usage_count__range=(21, 50))
            case "very_high":
                return qs.filter(usage_count__gt=50)
            case _:
                return qs


class ContentTypeFilter(DropdownFilter):
    title = _("Content Type")
    parameter_name = "content_type"

    def lookups(self, request, model_admin):
        types = (
            ContentType.objects.filter(taggeditem__isnull=False)
            .distinct()
            .values_list("id", "model")
        )
        return [(cid, model.title()) for cid, model in types]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(
                taggeditem__content_type_id=self.value()
            ).distinct()
        return queryset


class TagInLine(GenericTabularInline):
    model = TaggedItem
    autocomplete_fields = ["tag"]
    extra = 0
    fields = ("tag",)
    verbose_name = _("Tag")
    verbose_name_plural = _("Tags")
    tab = True
    collapsible = True


@admin.register(Tag)
class TagAdmin(BaseTranslatableAdmin):
    list_display = (
        "label_display",
        "active",
        "usage_count_display",
        "sort_order",
        "created_display",
    )
    list_filter = [
        TagStatusFilter,
        TagUsageFilter,
        ContentTypeFilter,
        "active",
        ("created_at", RangeDateTimeFilter),
        ("updated_at", RangeDateTimeFilter),
    ]
    list_editable = ("active",)
    search_fields = ["translations__label", "id"]
    ordering_field = "sort_order"
    hide_ordering_field = True
    readonly_fields = ("id", "uuid", "created_at", "updated_at")
    list_per_page = 50
    ordering = ["sort_order", "-id"]
    actions = [
        "activate_tags",
        "deactivate_tags",
        "update_sort_order",
        "analyze_usage",
    ]

    fieldsets = (
        (
            _("Tag Information"),
            {"fields": ("label", "active"), "classes": ("wide",)},
        ),
        (_("Organization"), {"fields": ("sort_order",), "classes": ("wide",)}),
        (
            _("System Information"),
            {
                "fields": ("id", "uuid", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(usage_count=Count("taggeditem", distinct=True))
        )

    @admin.display(description=_("Name"), ordering="translations__label")
    def label_display(self, obj):
        return obj.safe_translation_getter("label", any_language=True) or _(
            "Unnamed Tag"
        )

    @admin.display(description=_("Usage"), ordering="usage_count")
    def usage_count_display(self, obj):
        return getattr(obj, "usage_count", 0)

    @admin.display(description=_("Created"))
    def created_display(self, obj):
        return format_dt(obj.created_at)

    @action(
        description=str(_("Activate selected tags")),
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def activate_tags(self, request, queryset):
        updated = queryset.update(active=True)
        self.message_user(
            request,
            ngettext(
                "%(count)d tag was activated.",
                "%(count)d tags were activated.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=str(_("Deactivate selected tags")),
        variant=ActionVariant.WARNING,
        icon="cancel",
    )
    def deactivate_tags(self, request, queryset):
        updated = queryset.update(active=False)
        self.message_user(
            request,
            ngettext(
                "%(count)d tag was deactivated.",
                "%(count)d tags were deactivated.",
                updated,
            )
            % {"count": updated},
            messages.WARNING,
        )

    @action(
        description=str(_("Update sort order")),
        variant=ActionVariant.INFO,
        icon="sort",
    )
    def update_sort_order(self, request, queryset):
        ordered = queryset.annotate(
            usage_count=models.Count("taggeditem")
        ).order_by("-usage_count", "translations__label")
        updated = 0
        for idx, tag in enumerate(ordered):
            tag.sort_order = idx
            tag.save(update_fields=["sort_order"])
            updated += 1
        self.message_user(
            request,
            _("Updated sort order for %(count)d tags.") % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=str(_("Analyze tag usage")),
        variant=ActionVariant.PRIMARY,
        icon="analytics",
    )
    def analyze_usage(self, request, queryset):
        total = queryset.count()
        active = queryset.filter(active=True).count()
        used = queryset.filter(taggeditem__isnull=False).distinct().count()
        self.message_user(
            request,
            _(
                "Analysis complete: %(total)d total tags, %(active)d "
                "active, %(used)d in use."
            )
            % {"total": total, "active": active, "used": used},
            messages.INFO,
        )


@admin.register(TaggedItem)
class TaggedItemAdmin(BaseModelAdmin):
    list_display = (
        "tag_display",
        "content_object_display",
        "content_type_display",
        "created_at",
    )
    list_filter = [
        ("tag", RelatedDropdownFilter),
        ("content_type", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    ]
    search_fields = ["tag__translations__label", "object_id"]
    readonly_fields = ("uuid", "created_at", "updated_at", "content_object")
    list_select_related = ["tag", "content_type"]
    list_per_page = 100
    ordering = ["-created_at"]

    fieldsets = (
        (
            _("Tagged Item Information"),
            {
                "fields": (
                    "tag",
                    "content_type",
                    "object_id",
                    "content_object",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": ("uuid", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("tag", "content_type")
            .prefetch_related("tag__translations")
        )

    @admin.display(description=_("Tag"), ordering="tag__translations__label")
    def tag_display(self, obj):
        return obj.tag.safe_translation_getter("label", any_language=True) or _(
            "Unnamed Tag"
        )

    @admin.display(description=_("Content Object"))
    def content_object_display(self, obj):
        try:
            content_object = obj.content_object
        except Exception:
            return "—"
        return str(content_object) if content_object else "—"

    @admin.display(description=_("Content Type"), ordering="content_type")
    def content_type_display(self, obj):
        return obj.content_type.model.title()


class TaggedItemInline(GenericTabularInline):
    model = TaggedItem
    extra = 0
    fields = ("tag",)
    verbose_name = _("Tag")
    verbose_name_plural = _("Tags")
    ct_field = "content_type"
    ct_fk_field = "object_id"
    tab = True
