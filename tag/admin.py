from datetime import timedelta

from django.contrib import admin, messages
from django.contrib.contenttypes.admin import GenericTabularInline
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from parler.admin import TranslatableAdmin
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateTimeFilter,
)
from unfold.decorators import action
from unfold.enums import ActionVariant

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
        filter_value = self.value()

        match filter_value:
            case "active":
                filter_kwargs = {"active": True}
            case "inactive":
                filter_kwargs = {"active": False}
            case "used":
                return queryset.filter(taggeditem__isnull=False).distinct()
            case "unused":
                filter_kwargs = {"taggeditem__isnull": True}
            case "popular":
                return queryset.annotate(
                    usage_count=models.Count("taggeditem")
                ).filter(usage_count__gt=10)
            case "recent":
                seven_days_ago = timezone.now() - timedelta(days=7)
                filter_kwargs = {"created_at__gte": seven_days_ago}
            case _:
                return queryset

        return queryset.filter(**filter_kwargs)


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
        filter_value = self.value()

        queryset = queryset.annotate(usage_count=models.Count("taggeditem"))

        match filter_value:
            case "single":
                filter_kwargs = {"usage_count": 1}
            case "low":
                filter_kwargs = {"usage_count__range": (2, 5)}
            case "medium":
                filter_kwargs = {"usage_count__range": (6, 20)}
            case "high":
                filter_kwargs = {"usage_count__range": (21, 50)}
            case "very_high":
                filter_kwargs = {"usage_count__gt": 50}
            case _:
                return queryset

        return queryset.filter(**filter_kwargs)


class ContentTypeFilter(DropdownFilter):
    title = _("Content Type")
    parameter_name = "content_type"

    def lookups(self, request, model_admin):
        content_types = (
            ContentType.objects.filter(taggeditem__isnull=False)
            .distinct()
            .values_list("id", "model")
        )

        return [(ct_id, ct_model.title()) for ct_id, ct_model in content_types]

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
    verbose_name = "Tag"
    verbose_name_plural = "Tags"

    classes = ["collapse"]


@admin.register(Tag)
class TagAdmin(ModelAdmin, TranslatableAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

    list_display = [
        "tag_info",
        "status_badge",
        "usage_stats",
        "content_distribution",
        "sort_display",
        "created_display",
    ]
    list_filter = [
        TagStatusFilter,
        TagUsageFilter,
        ContentTypeFilter,
        "active",
        ("created_at", RangeDateTimeFilter),
        ("updated_at", RangeDateTimeFilter),
    ]
    search_fields = [
        "translations__label",
        "id",
    ]
    readonly_fields = (
        "id",
        "uuid",
        "sort_order",
        "created_at",
        "updated_at",
        "tag_analytics",
        "usage_analytics",
        "content_analytics",
    )
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
            {
                "fields": ("label", "active"),
                "classes": ("wide",),
            },
        ),
        (
            _("Organization"),
            {
                "fields": ("sort_order",),
                "classes": ("wide",),
            },
        ),
        (
            _("Analytics"),
            {
                "fields": (
                    "tag_analytics",
                    "usage_analytics",
                    "content_analytics",
                ),
                "classes": ("collapse",),
            },
        ),
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
            .annotate(
                usage_count=models.Count("taggeditem"),
                content_types_count=models.Count(
                    "taggeditem__content_type", distinct=True
                ),
            )
            .prefetch_related("taggeditem_set__content_type")
        )

    def tag_info(self, obj):
        label = (
            obj.safe_translation_getter("label", any_language=True)
            or "Unnamed Tag"
        )

        label_display = label[:30] + "..." if len(label) > 30 else label

        usage_count = getattr(obj, "usage_count", 0)
        if usage_count > 0:
            usage_color = "text-green-600 dark:text-green-400"
            usage_icon = "🏷️"
        else:
            usage_color = "text-base-500 dark:text-base-400"
            usage_icon = "🏷️"

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100 flex items-center gap-2">'
            '<span class="{}">{}</span>'
            "<span>{}</span>"
            "</div>"
            '<div class="text-base-600 dark:text-base-400">ID: {}</div>'
            '<div class="text-xs text-base-500 dark:text-base-400">Sort: {}</div>'
            "</div>",
            usage_color,
            usage_icon,
            label_display,
            obj.id,
            obj.sort_order or "No order",
        )

    tag_info.short_description = _("Tag")

    def status_badge(self, obj):
        if obj.active:
            status_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200 rounded-full">'
                "✅ Active"
                "</span>"
            )
        else:
            status_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-200 rounded-full">'
                "❌ Inactive"
                "</span>"
            )

        label = obj.safe_translation_getter("label", any_language=True) or ""
        label_length = len(label)

        if label_length == 0:
            label_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-200 rounded mt-1">'
                "📝 No Label"
                "</span>"
            )
        elif label_length < 3:
            label_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-200 rounded mt-1">'
                "📝 Short"
                "</span>"
            )
        else:
            label_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200 rounded mt-1">'
                "📝 Good"
                "</span>"
            )

        return format_html(
            '<div class="space-y-1"><div>{}</div><div>{}</div></div>',
            status_badge,
            label_badge,
        )

    status_badge.short_description = _("Status")

    def usage_stats(self, obj):
        usage_count = getattr(obj, "usage_count", 0)

        if usage_count == 0:
            usage_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-base-50 dark:bg-base-800 text-base-700 dark:text-base-700 rounded">'
                "🚫 Unused"
                "</span>"
            )
        elif usage_count == 1:
            usage_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-200 rounded">'
                "⚠️ Single"
                "</span>"
            )
        elif usage_count <= 10:
            usage_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200 rounded">'
                "📊 Low"
                "</span>"
            )
        elif usage_count <= 50:
            usage_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200 rounded">'
                "📈 Popular"
                "</span>"
            )
        else:
            usage_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-200 rounded">'
                "🌟 Trending"
                "</span>"
            )

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{} uses</div>'
            '<div class="mt-1">{}</div>'
            "</div>",
            usage_count,
            usage_badge,
        )

    usage_stats.short_description = _("Usage")

    def content_distribution(self, obj):
        content_types_count = getattr(obj, "content_types_count", 0)

        if hasattr(obj, "taggeditem_set"):
            content_type_usage = {}
            for tagged_item in obj.taggeditem_set.all()[:10]:
                ct_name = tagged_item.content_type.model
                content_type_usage[ct_name] = (
                    content_type_usage.get(ct_name, 0) + 1
                )

            if content_type_usage:
                top_content_type = max(
                    content_type_usage, key=content_type_usage.get
                )
                top_count = content_type_usage[top_content_type]
            else:
                top_content_type = "None"
                top_count = 0
        else:
            top_content_type = "Unknown"
            top_count = 0

        if content_types_count == 0:
            diversity_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-base-50 dark:bg-base-800 text-base-700 dark:text-base-700 rounded">'
                "📊 No Data"
                "</span>"
            )
        elif content_types_count == 1:
            diversity_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200 rounded">'
                "📌 Focused"
                "</span>"
            )
        else:
            diversity_badge = format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200 rounded">'
                "🌐 Diverse"
                "</span>"
            )

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{} types</div>'
            '<div class="text-base-600 dark:text-base-400">{}: {}</div>'
            '<div class="mt-1">{}</div>'
            "</div>",
            content_types_count,
            top_content_type.title()
            if top_content_type != "None"
            else "No usage",
            top_count if top_count > 0 else "",
            diversity_badge,
        )

    content_distribution.short_description = _("Content Types")

    def sort_display(self, obj):
        sort_order = obj.sort_order

        if sort_order is None:
            badge_class = (
                "bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-200"
            )
            icon = "❌"
            label = "No Order"
        elif sort_order == 0:
            badge_class = (
                "bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200"
            )
            icon = "🥇"
            label = "First"
        else:
            badge_class = "bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200"
            icon = "📋"
            label = f"#{sort_order}"

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium {} rounded gap-1">'
            "<span>{}</span>"
            "<span>{}</span>"
            "</span>"
            "</div>",
            sort_order if sort_order is not None else "None",
            badge_class,
            icon,
            label,
        )

    sort_display.short_description = _("Sort Order")

    def created_display(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="text-base-600 dark:text-base-400">{}</div>'
            "</div>",
            obj.created_at.strftime("%Y-%m-%d"),
            obj.created_at.strftime("%H:%M"),
        )

    created_display.short_description = _("Created")

    def tag_analytics(self, obj):
        label = obj.safe_translation_getter("label", any_language=True) or ""
        label_length = len(label)
        usage_count = TaggedItem.objects.filter(tag=obj).count()

        words = label.split() if label else []
        word_count = len(words)

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Label Length:</strong></div><div>{} chars</div>"
            "<div><strong>Word Count:</strong></div><div>{} words</div>"
            "<div><strong>Total Usage:</strong></div><div>{} items</div>"
            "<div><strong>Active Status:</strong></div><div>{}</div>"
            "<div><strong>Has Label:</strong></div><div>{}</div>"
            "<div><strong>Sort Position:</strong></div><div>{}</div>"
            "</div>"
            "</div>",
            label_length,
            word_count,
            usage_count,
            "Active" if obj.active else "Inactive",
            "Yes" if label else "No",
            obj.sort_order if obj.sort_order is not None else "Not Set",
        )

    tag_analytics.short_description = _("Tag Analytics")

    def usage_analytics(self, obj):
        tagged_items = TaggedItem.objects.filter(tag=obj)
        total_usage = tagged_items.count()

        content_types = (
            tagged_items.values("content_type__model")
            .annotate(count=models.Count("id"))
            .order_by("-count")[:3]
        )

        thirty_days_ago = timezone.now() - timedelta(days=30)
        recent_usage = tagged_items.filter(
            created_at__gte=thirty_days_ago
        ).count()

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Total Usage:</strong></div><div>{}</div>"
            "<div><strong>Recent Usage:</strong></div><div>{} (30d)</div>"
            "<div><strong>Content Types:</strong></div><div>{}</div>"
            "<div><strong>Usage Rate:</strong></div><div>{}</div>"
            "</div>"
            "</div>",
            total_usage,
            recent_usage,
            len(content_types),
            "High"
            if total_usage > 20
            else "Medium"
            if total_usage > 5
            else "Low",
        )

    usage_analytics.short_description = _("Usage Analytics")

    def content_analytics(self, obj):
        tagged_items = TaggedItem.objects.filter(tag=obj)

        content_type_stats = {}
        for item in tagged_items.select_related("content_type")[:50]:
            ct_name = item.content_type.model
            content_type_stats[ct_name] = content_type_stats.get(ct_name, 0) + 1

        diversity_score = len(content_type_stats)
        most_used_type = (
            max(content_type_stats, key=content_type_stats.get)
            if content_type_stats
            else "None"
        )
        most_used_count = (
            content_type_stats.get(most_used_type, 0)
            if most_used_type != "None"
            else 0
        )

        return format_html(
            '<div class="text-sm">'
            '<div class="grid grid-cols-2 gap-2">'
            "<div><strong>Diversity Score:</strong></div><div>{} types</div>"
            "<div><strong>Most Used Type:</strong></div><div>{}</div>"
            "<div><strong>Type Usage:</strong></div><div>{} items</div>"
            "<div><strong>Specialization:</strong></div><div>{}</div>"
            "</div>"
            "</div>",
            diversity_score,
            most_used_type.title() if most_used_type != "None" else "No data",
            most_used_count,
            "Specialized"
            if diversity_score == 1
            else "General"
            if diversity_score > 3
            else "Mixed",
        )

    content_analytics.short_description = _("Content Analytics")

    @action(
        description=_("Activate selected tags"),
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def activate_tags(self, request, queryset):
        updated = queryset.update(active=True)
        self.message_user(
            request,
            ngettext(
                _("%(count)d tag was activated."),
                _("%(count)d tags were activated."),
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=_("Deactivate selected tags"),
        variant=ActionVariant.WARNING,
        icon="cancel",
    )
    def deactivate_tags(self, request, queryset):
        updated = queryset.update(active=False)
        self.message_user(
            request,
            ngettext(
                _("%(count)d tag was deactivated."),
                _("%(count)d tags were deactivated."),
                updated,
            )
            % {"count": updated},
            messages.WARNING,
        )

    @action(
        description=_("Update sort order"),
        variant=ActionVariant.INFO,
        icon="sort",
    )
    def update_sort_order(self, request, queryset):
        tags_with_usage = queryset.annotate(
            usage_count=models.Count("taggeditem")
        ).order_by("-usage_count", "translations__label")

        updated = 0
        for index, tag in enumerate(tags_with_usage):
            tag.sort_order = index
            tag.save(update_fields=["sort_order"])
            updated += 1

        self.message_user(
            request,
            _("Updated sort order for %(count)d tags.") % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=_("Analyze tag usage"),
        variant=ActionVariant.PRIMARY,
        icon="analytics",
    )
    def analyze_usage(self, request, queryset):
        total_tags = queryset.count()
        active_tags = queryset.filter(active=True).count()
        used_tags = queryset.filter(taggeditem__isnull=False).distinct().count()

        self.message_user(
            request,
            _(
                "Analysis complete: %(total)d total tags, %(active)d active, %(used)d in use."
            )
            % {"total": total_tags, "active": active_tags, "used": used_tags},
            messages.INFO,
        )


@admin.register(TaggedItem)
class TaggedItemAdmin(ModelAdmin):
    list_fullwidth = True
    list_filter_submit = True

    list_display = [
        "tagged_item_info",
        "tag_display",
        "content_object_display",
        "content_type_badge",
        "created_display",
    ]
    list_filter = [
        ("tag", admin.RelatedOnlyFieldListFilter),
        ("content_type", admin.RelatedOnlyFieldListFilter),
        ("created_at", RangeDateTimeFilter),
    ]
    search_fields = [
        "tag__translations__label",
        "object_id",
    ]
    readonly_fields = (
        "uuid",
        "created_at",
        "updated_at",
        "content_object",
    )
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

    def tagged_item_info(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">Item #{}</div>'
            '<div class="text-base-600 dark:text-base-400">Object ID: {}</div>'
            '<div class="text-xs text-base-500 dark:text-base-400">UUID: {}</div>'
            "</div>",
            obj.id,
            obj.object_id,
            str(obj.uuid)[:8],
        )

    tagged_item_info.short_description = _("Tagged Item")

    def tag_display(self, obj):
        tag = obj.tag
        label = (
            tag.safe_translation_getter("label", any_language=True)
            or "Unnamed Tag"
        )

        if tag.active:
            status_color = "text-green-600 dark:text-green-400"
            status_icon = "✅"
        else:
            status_color = "text-red-600 dark:text-red-400"
            status_icon = "❌"

        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="flex items-center gap-1 {}">'
            "<span>{}</span>"
            "<span>Tag #{}</span>"
            "</div>"
            "</div>",
            label,
            status_color,
            status_icon,
            tag.id,
        )

    tag_display.short_description = _("Tag")

    def content_object_display(self, obj):
        try:
            content_obj = obj.content_object
            if content_obj:
                obj_str = str(content_obj)[:40]
                if len(str(content_obj)) > 40:
                    obj_str += "..."

                return format_html(
                    '<div class="text-sm">'
                    '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
                    '<div class="text-base-600 dark:text-base-400">ID: {}</div>'
                    "</div>",
                    obj_str,
                    obj.object_id,
                )
            else:
                return format_html(
                    '<div class="text-sm text-red-600 dark:text-red-400">Object not found</div>'
                )
        except Exception:
            return format_html(
                '<div class="text-sm text-red-600 dark:text-red-400">Error loading object</div>'
            )

    content_object_display.short_description = _("Content Object")

    def content_type_badge(self, obj):
        content_type = obj.content_type
        model_name = content_type.model

        type_configs = {
            "product": {
                "bg": "bg-green-50 dark:bg-green-900",
                "text": "text-green-700 dark:text-green-200",
                "icon": "🏷️",
            },
            "article": {
                "bg": "bg-blue-50 dark:bg-blue-900",
                "text": "text-blue-700 dark:text-blue-200",
                "icon": "📄",
            },
            "user": {
                "bg": "bg-purple-50 dark:bg-purple-900",
                "text": "text-purple-700 dark:text-purple-200",
                "icon": "👤",
            },
            "category": {
                "bg": "bg-orange-50 dark:bg-orange-900",
                "text": "text-orange-700 dark:text-orange-200",
                "icon": "📁",
            },
        }

        config = type_configs.get(
            model_name,
            {
                "bg": "bg-base-50 dark:bg-base-800",
                "text": "text-base-700 dark:text-base-700",
                "icon": "📦",
            },
        )

        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium {} {} rounded border gap-1">'
            "<span>{}</span>"
            "<span>{}</span>"
            "</span>",
            config["bg"],
            config["text"],
            config["icon"],
            model_name.title(),
        )

    content_type_badge.short_description = _("Content Type")

    def created_display(self, obj):
        return format_html(
            '<div class="text-sm">'
            '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
            '<div class="text-base-600 dark:text-base-400">{}</div>'
            "</div>",
            obj.created_at.strftime("%Y-%m-%d"),
            obj.created_at.strftime("%H:%M"),
        )

    created_display.short_description = _("Created")
