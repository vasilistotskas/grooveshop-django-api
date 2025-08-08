from datetime import timedelta
from django.contrib import admin, messages
from django.contrib.contenttypes.admin import GenericTabularInline
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _, ngettext
from parler.admin import TranslatableAdmin
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import DropdownFilter, RangeDateTimeFilter
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
    search_fields = ["translations__label", "id"]
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
            {"fields": ("label", "active"), "classes": ("wide",)},
        ),
        (_("Organization"), {"fields": ("sort_order",), "classes": ("wide",)}),
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
        label = obj.safe_translation_getter("label", any_language=True) or _(
            "Unnamed Tag"
        )
        display = label[:30] + ("…" if len(label) > 30 else "")
        usage = getattr(obj, "usage_count", 0)
        usage_color = (
            "text-green-600 dark:text-green-400"
            if usage > 0
            else "text-base-500 dark:text-base-400"
        )
        usage_icon = "🏷️"
        esc_color = conditional_escape(usage_color)
        esc_icon = conditional_escape(usage_icon)
        esc_label = conditional_escape(display)
        esc_id = conditional_escape(str(obj.id))
        esc_sort = conditional_escape(str(obj.sort_order or _("No order")))

        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100 flex items-center gap-2">'
            f'<span class="{esc_color}">{esc_icon}</span>'
            f"<span>{esc_label}</span>"
            "</div>"
            f'<div class="text-base-600 dark:text-base-400">ID: {esc_id}</div>'
            f'<div class="text-xs text-base-500 dark:text-base-400">Sort: {esc_sort}</div>'
            "</div>"
        )
        return mark_safe(html)

    tag_info.short_description = _("Tag")

    def status_badge(self, obj):
        if obj.active:
            status = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200 rounded-full">'
                "✅ Active"
                "</span>"
            )
        else:
            status = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-200 rounded-full">'
                "❌ Inactive"
                "</span>"
            )

        label = obj.safe_translation_getter("label", any_language=True) or ""
        length = len(label)
        if length == 0:
            label_badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-200 rounded mt-1">'
                "📝 No Label"
                "</span>"
            )
        elif length < 3:
            label_badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-200 rounded mt-1">'
                "📝 Short"
                "</span>"
            )
        else:
            label_badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200 rounded mt-1">'
                "📝 Good"
                "</span>"
            )

        html = (
            '<div class="space-y-1">'
            f"<div>{status}</div>"
            f"<div>{label_badge}</div>"
            "</div>"
        )
        return mark_safe(html)

    status_badge.short_description = _("Status")

    def usage_stats(self, obj):
        usage = getattr(obj, "usage_count", 0)
        if usage == 0:
            badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-base-50 dark:bg-base-800 text-base-700 dark:text-base-700 rounded">'
                "🚫 Unused"
                "</span>"
            )
        elif usage == 1:
            badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-200 rounded">'
                "⚠️ Single"
                "</span>"
            )
        elif usage <= 10:
            badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200 rounded">'
                "📊 Low"
                "</span>"
            )
        elif usage <= 50:
            badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200 rounded">'
                "📈 Popular"
                "</span>"
            )
        else:
            badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-200 rounded">'
                "🌟 Trending"
                "</span>"
            )

        esc_usage = conditional_escape(str(usage))
        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{esc_usage} uses</div>'
            f'<div class="mt-1">{badge}</div>'
            "</div>"
        )
        return mark_safe(html)

    usage_stats.short_description = _("Usage")

    def content_distribution(self, obj):
        ct_count = getattr(obj, "content_types_count", 0)
        top_map = {}
        for ti in obj.taggeditem_set.all()[:10]:
            name = ti.content_type.model
            top_map[name] = top_map.get(name, 0) + 1
        if top_map:
            top, count = max(top_map.items(), key=lambda kv: kv[1])
        else:
            top, count = _("None"), 0

        if ct_count == 0:
            div_badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-base-50 dark:bg-base-800 text-base-700 dark:text-base-700 rounded">'
                "📊 No Data"
                "</span>"
            )
        elif ct_count == 1:
            div_badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200 rounded">'
                "📌 Focused"
                "</span>"
            )
        else:
            div_badge = mark_safe(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200 rounded">'
                "🌐 Diverse"
                "</span>"
            )

        esc_ct = conditional_escape(str(ct_count))
        esc_top = conditional_escape(
            top.title() if top != _("None") else _("No usage")
        )
        esc_count = conditional_escape(str(count or ""))

        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{esc_ct} types</div>'
            f'<div class="text-base-600 dark:text-base-400">{esc_top}: {esc_count}</div>'
            f'<div class="mt-1">{div_badge}</div>'
            "</div>"
        )
        return mark_safe(html)

    content_distribution.short_description = _("Content Types")

    def sort_display(self, obj):
        order = obj.sort_order
        if order is None:
            cls = "bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-200"
            icon, label = "❌", _("None")
        elif order == 0:
            cls = "bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-200"
            icon, label = "🥇", _("First")
        else:
            cls = "bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-200"
            icon, label = "📋", f"#{order}"

        esc_order = conditional_escape(
            str(order if order is not None else _("None"))
        )
        esc_cls = conditional_escape(cls)
        esc_icon = conditional_escape(icon)
        esc_label = conditional_escape(label)

        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{esc_order}</div>'
            f'<span class="inline-flex items-center px-2 py-1 text-xs font-medium {esc_cls} rounded gap-1">'
            f"<span>{esc_icon}</span><span>{esc_label}</span>"
            "</span>"
            "</div>"
        )
        return mark_safe(html)

    sort_display.short_description = _("Sort Order")

    def created_display(self, obj):
        d = obj.created_at.strftime("%Y-%m-%d")
        t = obj.created_at.strftime("%H:%M")
        esc_d = conditional_escape(d)
        esc_t = conditional_escape(t)
        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{esc_d}</div>'
            f'<div class="text-base-600 dark:text-base-400">{esc_t}</div>'
            "</div>"
        )
        return mark_safe(html)

    created_display.short_description = _("Created")

    def tag_analytics(self, obj):
        label = obj.safe_translation_getter("label", any_language=True) or ""
        chars = len(label)
        words = len(label.split())
        uses = TaggedItem.objects.filter(tag=obj).count()
        status = _("Active") if obj.active else _("Inactive")
        has_lbl = _("Yes") if label else _("No")
        sort_val = (
            obj.sort_order if obj.sort_order is not None else _("Not Set")
        )

        esc_chars = conditional_escape(str(chars))
        esc_words = conditional_escape(str(words))
        esc_uses = conditional_escape(str(uses))
        esc_status = conditional_escape(status)
        esc_has = conditional_escape(has_lbl)
        esc_sort = conditional_escape(str(sort_val))

        html = (
            '<div class="text-sm"><div class="grid grid-cols-2 gap-2">'
            f"<div><strong>Label Length:</strong></div><div>{esc_chars} chars</div>"
            f"<div><strong>Word Count:</strong></div><div>{esc_words} words</div>"
            f"<div><strong>Total Usage:</strong></div><div>{esc_uses} items</div>"
            f"<div><strong>Active Status:</strong></div><div>{esc_status}</div>"
            f"<div><strong>Has Label:</strong></div><div>{esc_has}</div>"
            f"<div><strong>Sort Position:</strong></div><div>{esc_sort}</div>"
            "</div></div>"
        )
        return mark_safe(html)

    tag_analytics.short_description = _("Tag Analytics")

    def usage_analytics(self, obj):
        items = TaggedItem.objects.filter(tag=obj)
        total = items.count()
        recent = items.filter(
            created_at__gte=timezone.now() - timedelta(days=30)
        ).count()
        types = items.values("content_type__model").distinct().count()
        rate = (
            _("High") if total > 20 else _("Medium") if total > 5 else _("Low")
        )

        esc_total = conditional_escape(str(total))
        esc_recent = conditional_escape(str(recent))
        esc_types = conditional_escape(str(types))
        esc_rate = conditional_escape(rate)

        html = (
            '<div class="text-sm"><div class="grid grid-cols-2 gap-2">'
            f"<div><strong>Total Usage:</strong></div><div>{esc_total}</div>"
            f"<div><strong>Recent Usage:</strong></div><div>{esc_recent} (30d)</div>"
            f"<div><strong>Content Types:</strong></div><div>{esc_types}</div>"
            f"<div><strong>Usage Rate:</strong></div><div>{esc_rate}</div>"
            "</div></div>"
        )
        return mark_safe(html)

    usage_analytics.short_description = _("Usage Analytics")

    def content_analytics(self, obj):
        items = TaggedItem.objects.filter(tag=obj).select_related(
            "content_type"
        )[:50]
        stats = {}
        for i in items:
            m = i.content_type.model
            stats[m] = stats.get(m, 0) + 1

        diversity = len(stats)
        top = max(stats, key=stats.get) if stats else _("None")
        top_count = stats.get(top, 0)
        spec = (
            _("Specialized")
            if diversity == 1
            else _("General")
            if diversity > 3
            else _("Mixed")
        )

        esc_div = conditional_escape(str(diversity))
        esc_top = conditional_escape(
            top.title() if top != _("None") else _("No data")
        )
        esc_count = conditional_escape(str(top_count))
        esc_spec = conditional_escape(spec)

        html = (
            '<div class="text-sm"><div class="grid grid-cols-2 gap-2">'
            f"<div><strong>Diversity Score:</strong></div><div>{esc_div} types</div>"
            f"<div><strong>Most Used Type:</strong></div><div>{esc_top}</div>"
            f"<div><strong>Type Usage:</strong></div><div>{esc_count} items</div>"
            f"<div><strong>Specialization:</strong></div><div>{esc_spec}</div>"
            "</div></div>"
        )
        return mark_safe(html)

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
        description=_("Analyze tag usage"),
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
                "Analysis complete: %(total)d total tags, %(active)d active, %(used)d in use."
            )
            % {"total": total, "active": active, "used": used},
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

    def tagged_item_info(self, obj):
        esc_id = conditional_escape(str(obj.id))
        esc_obj = conditional_escape(str(obj.object_id))
        esc_uuid = conditional_escape(str(obj.uuid)[:8])
        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">Item #{esc_id}</div>'
            f'<div class="text-base-600 dark:text-base-400">Object ID: {esc_obj}</div>'
            f'<div class="text-xs text-base-500 dark:text-base-400">UUID: {esc_uuid}</div>'
            "</div>"
        )
        return mark_safe(html)

    tagged_item_info.short_description = _("Tagged Item")

    def tag_display(self, obj):
        t = obj.tag
        label = t.safe_translation_getter("label", any_language=True) or _(
            "Unnamed Tag"
        )
        status_color = (
            "text-green-600 dark:text-green-400"
            if t.active
            else "text-red-600 dark:text-red-400"
        )
        status_icon = "✅" if t.active else "❌"
        esc_label = conditional_escape(label)
        esc_color = conditional_escape(status_color)
        esc_icon = conditional_escape(status_icon)
        esc_tid = conditional_escape(str(t.id))
        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{esc_label}</div>'
            f'<div class="flex items-center gap-1 {esc_color}">'
            f"<span>{esc_icon}</span><span>Tag #{esc_tid}</span>"
            "</div>"
            "</div>"
        )
        return mark_safe(html)

    tag_display.short_description = _("Tag")

    def content_object_display(self, obj):
        try:
            co = obj.content_object
            if co:
                text = str(co)
                display = text[:40] + ("…" if len(text) > 40 else "")
                esc_text = conditional_escape(display)
                esc_oid = conditional_escape(str(obj.object_id))
                html = (
                    '<div class="text-sm">'
                    f'<div class="font-medium text-base-900 dark:text-base-100">{esc_text}</div>'
                    f'<div class="text-base-600 dark:text-base-400">ID: {esc_oid}</div>'
                    "</div>"
                )
                return mark_safe(html)
            return mark_safe(
                '<div class="text-sm text-red-600 dark:text-red-400">Object not found</div>'
            )
        except Exception:
            return mark_safe(
                '<div class="text-sm text-red-600 dark:text-red-400">Error loading object</div>'
            )

    content_object_display.short_description = _("Content Object")

    def content_type_badge(self, obj):
        name = obj.content_type.model
        cfg = {
            "product": (
                "bg-green-50 dark:bg-green-900",
                "text-green-700 dark:text-green-200",
                "🏷️",
            ),
            "article": (
                "bg-blue-50 dark:bg-blue-900",
                "text-blue-700 dark:text-blue-200",
                "📄",
            ),
            "user": (
                "bg-purple-50 dark:bg-purple-900",
                "text-purple-700 dark:text-purple-200",
                "👤",
            ),
            "category": (
                "bg-orange-50 dark:bg-orange-900",
                "text-orange-700 dark:text-orange-200",
                "📁",
            ),
        }.get(
            name,
            (
                "bg-base-50 dark:bg-base-800",
                "text-base-700 dark:text-base-700",
                "📦",
            ),
        )
        bg, text, icon = cfg
        esc_bg = conditional_escape(bg)
        esc_text = conditional_escape(text)
        esc_icon = conditional_escape(icon)
        esc_name = conditional_escape(name.title())
        html = (
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            f'{esc_bg} {esc_text} rounded border gap-1">'
            f"<span>{esc_icon}</span><span>{esc_name}</span>"
            "</span>"
        )
        return mark_safe(html)

    content_type_badge.short_description = _("Content Type")

    def created_display(self, obj):
        d = obj.created_at.strftime("%Y-%m-%d")
        t = obj.created_at.strftime("%H:%M")
        esc_d = conditional_escape(d)
        esc_t = conditional_escape(t)
        html = (
            '<div class="text-sm">'
            f'<div class="font-medium text-base-900 dark:text-base-100">{esc_d}</div>'
            f'<div class="text-base-600 dark:text-base-400">{esc_t}</div>'
            "</div>"
        )
        return mark_safe(html)

    created_display.short_description = _("Created")


class TaggedItemInline(GenericTabularInline):
    model = TaggedItem
    extra = 0
    fields = ("tag",)
    verbose_name = _("Tag")
    verbose_name_plural = _("Tags")
    ct_field = "content_type"
    ct_fk_field = "object_id"
