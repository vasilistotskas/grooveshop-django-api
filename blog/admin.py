from django.contrib import admin
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from mptt.admin import DraggableMPTTAdmin
from parler.admin import TranslatableAdmin
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateTimeFilter,
    RangeNumericListFilter,
    RelatedDropdownFilter,
    SliderNumericFilter,
)
from unfold.decorators import action
from unfold.enums import ActionVariant

from admin.base import BaseModelAdmin
from core.admin import ExportActionMixin

from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.comment import BlogComment
from blog.models.post import BlogPost, BlogPostTranslation
from blog.models.tag import BlogTag


class LikesCountFilter(RangeNumericListFilter):
    title = _("Likes")
    parameter_name = "likes_count"

    def queryset(self, request, queryset):
        # Short-circuit when the filter is unused. Django admin
        # invokes every ``list_filter``'s ``queryset()`` on every
        # page load — without this guard ``with_likes_count()``
        # added a ``LEFT JOIN blog_blogpost_likes`` + GROUP BY to
        # the main fetch, exploding the BlogPost changelist from
        # ~80 to >1000 queries.
        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if not value_from and not value_to:
            return queryset

        queryset = queryset.with_likes_count()
        filters = {}
        if value_from:
            filters["likes_count__gte"] = value_from
        if value_to:
            filters["likes_count__lte"] = value_to
        return queryset.filter(**filters)

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class CommentsCountFilter(RangeNumericListFilter):
    title = _("Comments")
    parameter_name = "comments_count"

    def queryset(self, request, queryset):
        # Short-circuit — same rationale as ``LikesCountFilter`` above.
        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if not value_from and not value_to:
            return queryset

        queryset = queryset.with_comments_count(approved_only=True)
        filters = {}
        if value_from:
            filters["comments_count__gte"] = value_from
        if value_to:
            filters["comments_count__lte"] = value_to
        return queryset.filter(**filters)

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class TagsCountFilter(RangeNumericListFilter):
    title = _("Tags")
    parameter_name = "tags_count"

    def queryset(self, request, queryset):
        # Short-circuit — same rationale as ``LikesCountFilter`` above.
        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if not value_from and not value_to:
            return queryset

        queryset = queryset.with_tags_count(active_only=True)
        filters = {}
        if value_from:
            filters["tags_count__gte"] = value_from
        if value_to:
            filters["tags_count__lte"] = value_to
        return queryset.filter(**filters)

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class PostsCountFilter(RangeNumericListFilter):
    title = _("Posts")
    parameter_name = "posts_count"

    def queryset(self, request, queryset):
        # Short-circuit — same rationale as ``LikesCountFilter`` above.
        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if not value_from and not value_to:
            return queryset

        if hasattr(queryset.model, "blog_posts"):
            queryset = queryset.annotate(
                posts_count_annotation=Count("blog_posts", distinct=True)
            )
        else:
            queryset = queryset.annotate(
                posts_count_annotation=Count("posts", distinct=True)
            )
        filters = {}
        if value_from:
            filters["posts_count_annotation__gte"] = value_from
        if value_to:
            filters["posts_count_annotation__lte"] = value_to
        return queryset.filter(**filters)

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class PublishStatusFilter(DropdownFilter):
    title = _("Publish Status")
    parameter_name = "publish_status"

    def lookups(self, request, model_admin):
        return [
            ("published", _("Published")),
            ("draft", _("Draft")),
            ("scheduled", _("Scheduled")),
        ]

    def queryset(self, request, queryset):
        if self.value() == "published":
            return queryset.filter(
                is_published=True, published_at__lte=timezone.now()
            )
        elif self.value() == "draft":
            return queryset.filter(is_published=False)
        elif self.value() == "scheduled":
            return queryset.filter(
                is_published=True, published_at__gt=timezone.now()
            )
        return queryset


class BlogPostTranslationInline(TabularInline):
    model = BlogPostTranslation
    extra = 0
    fields = ("language_code", "title", "subtitle")
    show_change_link = True

    tab = True


class BlogCommentInline(TabularInline):
    model = BlogComment
    extra = 0
    fields = ("user", "content_preview", "approved", "created_at")
    readonly_fields = ("content_preview", "created_at")
    show_change_link = True

    tab = True

    @admin.display(description=_("Content Preview"))
    def content_preview(self, obj):
        content = (
            obj.safe_translation_getter("content", any_language=True) or ""
        )
        return content[:50] + "..." if len(content) > 50 else content


@admin.register(BlogAuthor)
class BlogAuthorAdmin(ModelAdmin, TranslatableAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = False
    list_filter_submit = True
    list_filter_sheet = True

    list_display = (
        "user_display",
        "bio_preview",
        "posts_count",
        "total_likes_display",
        "website_link",
    )
    search_fields = (
        "user__email",
        "user__username",
        "user__first_name",
        "user__last_name",
        "translations__bio",
    )
    list_select_related = ["user"]
    readonly_fields = ["id", "total_likes_display", "posts_count"]

    fieldsets = (
        (
            _("Basic Information"),
            {
                "fields": ("user", "website"),
                "classes": ("wide",),
            },
        ),
        (
            _("Biography"),
            {
                "fields": ("bio",),
                "classes": ("wide",),
            },
        ),
        (
            _("Statistics"),
            {
                "fields": ("posts_count", "total_likes_display"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description=_("User"))
    def user_display(self, obj):
        return format_html(
            '<div class="flex items-center gap-2">'
            '<strong class="text-base-900 dark:text-base-100">{name}</strong>'
            '<span class="text-base-600 dark:text-base-300">({email}</span>'
            "</div>",
            name=obj.user.full_name or obj.user.username,
            email=obj.user.email,
        )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(posts_count_ann=Count("blog_posts", distinct=True))
        )

    @admin.display(description=_("Bio"))
    def bio_preview(self, obj):
        bio = obj.safe_translation_getter("bio", any_language=True) or ""
        if len(bio) > 50:
            return f"{bio[:50]}..."
        return bio

    @admin.display(description=_("Posts"))
    def posts_count(self, obj):
        count = getattr(obj, "posts_count_ann", obj.blog_posts.count())
        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">'
            "{count}"
            "</span>",
            count=count,
        )

    @admin.display(description=_("Total Likes"))
    def total_likes_display(self, obj):
        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full gap-1">'
            "<span>❤️</span>"
            "<span>{total}</span>"
            "</span>",
            total=obj.total_likes_received,
        )

    @admin.display(description=_("Website"))
    def website_link(self, obj):
        if obj.website:
            return format_html(
                '<a href="{url}" target="_blank" '
                'class="inline-flex items-center gap-1 text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300">'
                "<span>🔗</span><span>Website</span>"
                "</a>",
                url=obj.website,
            )
        return "-"


@admin.register(BlogTag)
class BlogTagAdmin(ModelAdmin, TranslatableAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = False
    list_filter_submit = True
    list_filter_sheet = True

    list_display = (
        "name_display",
        "active_badge",
        "active",
        "posts_count_badge",
        "sort_order",
    )
    list_filter = ("active", PostsCountFilter)
    list_editable = ("active",)
    search_fields = ("translations__name",)
    ordering = ("sort_order",)
    ordering_field = "sort_order"
    hide_ordering_field = True

    fieldsets = (
        (
            _("Tag Information"),
            {
                "fields": ("name", "active"),
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
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(posts_count_ann=Count("blog_posts", distinct=True))
        )

    @admin.display(description=_("Name"))
    def name_display(self, obj):
        return format_html(
            '<strong class="text-base-900 dark:text-base-100">{name}</strong>',
            name=(
                obj.safe_translation_getter("name", any_language=True)
                or "Unnamed Tag"
            ),
        )

    @admin.display(description=_("Status"))
    def active_badge(self, obj):
        if obj.active:
            return mark_safe(
                '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full gap-1">'
                "<span>✓</span>"
                "<span>Active</span>"
                "</span>"
            )
        return mark_safe(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full gap-1">'
            "<span>✗</span>"
            "<span>Inactive</span>"
            "</span>"
        )

    @admin.display(description=_("Posts"))
    def posts_count_badge(self, obj):
        count = getattr(obj, "posts_count_ann", obj.blog_posts.count())
        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">'
            "{count}"
            "</span>",
            count=count,
        )


@admin.register(BlogCategory)
class BlogCategoryAdmin(ModelAdmin, TranslatableAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = False

    ordering_field = "sort_order"
    hide_ordering_field = True
    list_per_page = 15
    list_display = (
        "category_name",
        "parent_display",
        "category_image",
        "posts_count_display",
        "recursive_posts_display",
    )
    list_display_links = ("category_name",)
    list_select_related = ("parent",)
    search_fields = ("translations__name", "translations__description")
    readonly_fields = [
        "id",
        "posts_count_display",
        "recursive_posts_display",
    ]

    fieldsets = (
        (
            _("Category Information"),
            {
                "fields": ("name", "description", "parent"),
                "classes": ("wide",),
            },
        ),
        (
            _("URL"),
            {
                "fields": ("slug",),
                "classes": ("wide",),
            },
        ),
        (
            _("Media"),
            {
                "fields": ("image",),
                "classes": ("wide",),
            },
        ),
        (
            _("Organization"),
            {
                "fields": ("sort_order",),
                "classes": ("collapse",),
            },
        ),
        (
            _("Statistics"),
            {
                "fields": ("posts_count_display", "recursive_posts_display"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = BlogCategory.objects.add_related_count(
            qs, BlogPost, "category", "posts_cumulative_count", cumulative=True
        )
        qs = BlogCategory.objects.add_related_count(
            qs, BlogPost, "category", "posts_count", cumulative=False
        )
        return qs

    def get_prepopulated_fields(self, request, obj=None):
        return {
            "slug": ("name",),
        }

    @admin.display(description=_("Name"), ordering="translations__name")
    def category_name(self, obj):
        return (
            obj.safe_translation_getter("name", any_language=True)
            or f"#{obj.pk}"
        )

    @admin.display(description=_("Parent"))
    def parent_display(self, obj):
        if obj.parent_id:
            return (
                obj.parent.safe_translation_getter("name", any_language=True)
                or f"#{obj.parent_id}"
            )
        return mark_safe(
            '<span class="text-base-500 dark:text-base-400">—</span>'
        )

    @admin.display(description=_("Image"))
    def category_image(self, obj):
        if obj.image:
            return format_html(
                '<img src="{url}" '
                'style="max-height: 40px; max-width: 80px; border-radius: 4px;" />',
                url=obj.image.url,
            )
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300">No image</span>'
        )

    @admin.display(description=_("Direct Posts"))
    def posts_count_display(self, instance):
        count = getattr(instance, "posts_count", 0)
        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-semibold '
            'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">'
            "{count}"
            "</span>",
            count=count,
        )

    @admin.display(description=_("Total Posts (Tree)"))
    def recursive_posts_display(self, instance):
        count = getattr(instance, "posts_cumulative_count", 0)
        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            'bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-300 rounded-full">'
            "{count}"
            "</span>",
            count=count,
        )


@admin.register(BlogPost)
class BlogPostAdmin(ExportActionMixin, BaseModelAdmin, TranslatableAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True
    list_horizontal_scrollbar_top = False

    list_display = (
        "title_display",
        "category_badge",
        "author_display",
        "featured_badge",
        "featured",
        "is_published",
        "publish_status_badge",
        "engagement_metrics",
        "published_at",
        "image_preview",
    )
    list_filter = (
        "featured",
        PublishStatusFilter,
        ("published_at", RangeDateTimeFilter),
        ("created_at", RangeDateTimeFilter),
        ("category", RelatedDropdownFilter),
        ("author", RelatedDropdownFilter),
        ("tags", RelatedDropdownFilter),
        ("view_count", SliderNumericFilter),
        LikesCountFilter,
        CommentsCountFilter,
        TagsCountFilter,
    )
    list_editable = (
        "featured",
        "is_published",
    )
    search_fields = (
        "translations__title",
        "translations__subtitle",
        "slug",
        "translations__body",
        "author__user__email",
        "author__user__username",
    )
    search_help_text = _(
        "Search by title, subtitle, slug, body, or author email/username."
    )
    autocomplete_fields = ["category", "author", "tags"]
    readonly_fields = [
        "id",
        "view_count",
        "created_at",
        "updated_at",
        "engagement_metrics",
        "seo_score",
    ]
    filter_horizontal = ["tags"]
    actions = [
        "mark_as_featured",
        "unmark_as_featured",
        "publish_posts",
        "unpublish_posts",
        "increment_view_count",
        "reset_view_count",
        "export_csv",
        "export_xml",
    ]
    date_hierarchy = "published_at"
    save_on_top = True
    list_per_page = 25
    list_select_related = ["category", "author", "author__user"]

    fieldsets = (
        (
            _("Content"),
            {
                "fields": ("title", "subtitle", "body"),
                "classes": ("wide",),
            },
        ),
        (
            _("Media"),
            {
                "fields": ("image",),
                "classes": ("wide",),
            },
        ),
        (
            _("Organization"),
            {
                "fields": ("slug", "category", "tags"),
                "classes": ("wide",),
            },
        ),
        (
            _("Publishing"),
            {
                "fields": (
                    "author",
                    "featured",
                    "is_published",
                    "published_at",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Engagement"),
            {
                "fields": ("view_count", "engagement_metrics"),
                "classes": ("collapse",),
            },
        ),
        (
            _("SEO"),
            {
                "fields": (
                    "seo_title",
                    "seo_description",
                    "seo_keywords",
                    "seo_score",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            _("System"),
            {
                "fields": ("id", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    inlines = [BlogPostTranslationInline, BlogCommentInline]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .with_likes_count()
            .with_comments_count()
            .with_tags_count()
        )

    @admin.display(description=_("Title"))
    def title_display(self, obj):
        title = (
            obj.safe_translation_getter("title", any_language=True)
            or "Untitled"
        )
        return format_html(
            '<strong class="text-base-900 dark:text-base-100">{title}</strong>',
            title=title[:50],
        )

    @admin.display(description=_("Category"))
    def category_badge(self, obj):
        if obj.category:
            category_name = (
                obj.category.safe_translation_getter("name", any_language=True)
                or "Unnamed"
            )
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
                'bg-indigo-50 dark:bg-indigo-900 text-indigo-700 dark:text-indigo-300 rounded-full">'
                "{name}"
                "</span>",
                name=category_name,
            )
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300">No category</span>'
        )

    @admin.display(description=_("Author"))
    def author_display(self, obj):
        if obj.author:
            author_name = obj.author.user.full_name or obj.author.user.username
            return format_html(
                '<span class="font-medium text-base-700 dark:text-base-300">{name}</span>',
                name=author_name,
            )
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300">No author</span>'
        )

    @admin.display(description=_("Featured"))
    def featured_badge(self, obj):
        if obj.featured:
            return mark_safe(
                '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
                'bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 rounded-full gap-1">'
                "<span>⭐</span>"
                "<span>Featured</span>"
                "</span>"
            )
        return ""

    @admin.display(description=_("Status"))
    def publish_status_badge(self, obj):
        if obj.is_published and obj.published_at:
            if obj.published_at <= timezone.now():
                return mark_safe(
                    '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
                    'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full gap-1">'
                    "<span>✓</span>"
                    "<span>Published</span>"
                    "</span>"
                )
            return mark_safe(
                '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
                'bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full gap-1">'
                "<span>📅</span>"
                "<span>Scheduled</span>"
                "</span>"
            )
        return mark_safe(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            'bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full gap-1">'
            "<span>📝</span>"
            "<span>Draft</span>"
            "</span>"
        )

    @admin.display(description=_("Engagement"))
    def engagement_metrics(self, obj):
        return format_html(
            '<div class="text-sm text-base-700 dark:text-base-300 flex items-center gap-3">'
            '<span class="flex items-center gap-1 text-red-600 dark:text-red-400">'
            "<span>❤️</span><span>{likes}</span>"
            "</span>"
            '<span class="flex items-center gap-1 text-blue-600 dark:text-blue-400">'
            "<span>💬</span><span>{comments}</span>"
            "</span>"
            '<span class="flex items-center gap-1 text-green-600 dark:text-green-400">'
            "<span>👀</span><span>{views}</span>"
            "</span>"
            "</div>",
            likes=obj.likes_count,
            comments=obj.comments_count,
            views=obj.view_count,
        )

    @admin.display(description=_("SEO Score"))
    def seo_score(self, obj):
        score = 0
        title = obj.safe_translation_getter("title", any_language=True) or ""
        subtitle = (
            obj.safe_translation_getter("subtitle", any_language=True) or ""
        )

        if len(title) > 10 and len(title) < 60:
            score += 25
        if subtitle:
            score += 25
        if obj.image:
            score += 25
        if obj.tags.exists():
            score += 25

        if score >= 75:
            bg_class = "bg-green-50 dark:bg-green-900"
            text_class = "text-green-700 dark:text-green-300"
        elif score >= 50:
            bg_class = "bg-yellow-50 dark:bg-yellow-900"
            text_class = "text-yellow-700 dark:text-yellow-300"
        else:
            bg_class = "bg-red-50 dark:bg-red-900"
            text_class = "text-red-700 dark:text-red-300"

        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium '
            '{bg_class} {text_class} rounded-full">'
            "{score}%"
            "</span>",
            bg_class=bg_class,
            text_class=text_class,
            score=score,
        )

    @admin.display(description="")
    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{url}" '
                'style="max-height: 100px; max-width: 80px; border-radius: 4px; object-fit: cover;" />',
                url=obj.image.url,
            )
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300">No image</span>'
        )

    def get_prepopulated_fields(self, request, obj=None):
        return {
            "slug": ("title",),
        }

    @action(
        description=str(_("Mark selected posts as featured")),
        variant=ActionVariant.PRIMARY,
        icon="star",
    )
    @transaction.atomic
    def mark_as_featured(self, request, queryset):
        updated = queryset.update(featured=True)
        self.message_user(
            request,
            _("%(count)d posts were successfully marked as featured.")
            % {"count": updated},
        )

    @action(
        description=str(_("Remove featured mark from selected posts")),
        variant=ActionVariant.WARNING,
        icon="star_border",
    )
    @transaction.atomic
    def unmark_as_featured(self, request, queryset):
        updated = queryset.update(featured=False)
        self.message_user(
            request,
            _("%(count)d posts were successfully unmarked as featured.")
            % {"count": updated},
        )

    @action(
        description=str(_("Publish selected posts")),
        variant=ActionVariant.SUCCESS,
        icon="publish",
    )
    @transaction.atomic
    def publish_posts(self, request, queryset):
        updated = queryset.update(
            is_published=True, published_at=timezone.now()
        )
        self.message_user(
            request,
            _("%(count)d posts were successfully published.")
            % {"count": updated},
        )

    @action(
        description=str(_("Unpublish selected posts")),
        variant=ActionVariant.WARNING,
        icon="unpublished",
    )
    @transaction.atomic
    def unpublish_posts(self, request, queryset):
        updated = queryset.update(is_published=False)
        self.message_user(
            request,
            _("%(count)d posts were successfully unpublished.")
            % {"count": updated},
        )

    @action(
        description=str(_("Increment view count by 100")),
        variant=ActionVariant.INFO,
        icon="visibility",
    )
    def increment_view_count(self, request, queryset):
        for post in queryset:
            post.view_count += 100
            post.save(update_fields=["view_count"])

        self.message_user(
            request,
            _("View count increased by 100 for %(count)d posts.")
            % {"count": queryset.count()},
        )

    @action(
        description=str(_("Reset view count to zero")),
        variant=ActionVariant.WARNING,
        icon="visibility_off",
    )
    def reset_view_count(self, request, queryset):
        updated = queryset.update(view_count=0)
        self.message_user(
            request,
            _("View count reset to zero for %(count)d posts.")
            % {"count": updated},
        )


@admin.register(BlogComment)
class BlogCommentAdmin(ModelAdmin, TranslatableAdmin, DraggableMPTTAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

    mptt_indent_field = "user"
    list_per_page = 30
    list_display = (
        "tree_actions",
        "indented_title",
        "user_display",
        "post_link",
        "approval_badge",
        "approved",
        "engagement_display",
        "created_at",
    )
    list_display_links = ("indented_title",)
    date_hierarchy = "created_at"
    search_fields = (
        "user__email",
        "user__username",
        "user__first_name",
        "user__last_name",
        "post__translations__title",
        "post__translations__subtitle",
        "translations__content",
    )
    list_filter = (
        "approved",
        ("created_at", RangeDateTimeFilter),
        ("post", RelatedDropdownFilter),
        ("user", RelatedDropdownFilter),
    )
    list_select_related = ["post", "user", "parent"]
    list_editable = ("approved",)
    actions = [
        "approve_comments",
        "unapprove_comments",
        "mark_as_spam",
    ]

    fieldsets = (
        (
            _("Comment Content"),
            {
                "fields": ("content",),
                "classes": ("wide",),
            },
        ),
        (
            _("Relations"),
            {
                "fields": ("post", "user", "parent"),
                "classes": ("wide",),
            },
        ),
        (
            _("Moderation"),
            {
                "fields": ("approved",),
                "classes": ("wide",),
            },
        ),
        (
            _("Engagement"),
            {
                "fields": ("engagement_display",),
                "classes": ("collapse",),
            },
        ),
    )

    readonly_fields = ["engagement_display"]

    def get_queryset(self, request):
        # NOTE: I tried annotating ``likes_count`` and ``replies_count``
        # to short-circuit the per-row property fallback (35 extra
        # queries on a 30-comment page), but the resulting JOIN +
        # GROUP BY on the main fetch was empirically more expensive
        # than the 35 single-row COUNTs (~1ms each). Left here as a
        # paper trail for the next person who's tempted. If we ever
        # add a real ``BlogCommentQuerySet.with_likes_count()`` using
        # ``Subquery`` (cheaper than JOIN explosion), wire it in here.
        # The model properties already check ``__dict__`` first
        # (see ``blog/models/comment.py``) so an annotation by name
        # will short-circuit them.
        return (
            super()
            .get_queryset(request)
            .select_related("post", "user", "parent")
        )

    @admin.display(description=_("User"))
    def user_display(self, obj):
        if obj.user:
            display_name = obj.user.full_name or obj.user.username
            return format_html(
                '<div class="text-sm">'
                '<div class="font-medium text-base-900 dark:text-base-100">{name}</div>'
                '<div class="text-base-600 dark:text-base-300">{email}</div>'
                "</div>",
                name=display_name,
                email=obj.user.email,
            )
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300 italic">Anonymous</span>'
        )

    @admin.display(description=_("Post"))
    def post_link(self, obj):
        if obj.post:
            title = (
                obj.post.safe_translation_getter("title", any_language=True)
                or f"Post {obj.post.id}"
            )
            title_display = title[:30] + "..." if len(title) > 30 else title
            return format_html(
                '<a href="{url}" '
                'class="text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 font-medium">'
                "{title}"
                "</a>",
                url=f"/admin/blog/blogpost/{obj.post.id}/change/",
                title=title_display,
            )
        return mark_safe(
            '<span class="text-base-600 dark:text-base-300">No post</span>'
        )

    @admin.display(description=_("Status"))
    def approval_badge(self, obj):
        if obj.approved:
            return mark_safe(
                '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
                'bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full gap-1">'
                "<span>✓</span>"
                "<span>Approved</span>"
                "</span>"
            )
        return mark_safe(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium '
            'bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full gap-1">'
            "<span>⏳</span>"
            "<span>Pending</span>"
            "</span>"
        )

    @admin.display(description=_("Engagement"))
    def engagement_display(self, obj):
        return format_html(
            '<div class="text-sm text-base-700 dark:text-base-300 flex items-center gap-3">'
            '<span class="flex items-center gap-1 text-red-600 dark:text-red-400">'
            "<span>❤️</span><span>{likes}</span>"
            "</span>"
            '<span class="flex items-center gap-1 text-blue-600 dark:text-blue-400">'
            "<span>💬</span><span>{replies}</span>"
            "</span>"
            "</div>",
            likes=obj.likes_count,
            replies=obj.replies_count,
        )

    @action(
        description=str(_("Approve selected comments")),
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    @transaction.atomic
    def approve_comments(self, request, queryset):
        updated = queryset.update(approved=True)
        self.message_user(
            request,
            _("%(count)d comments were successfully approved.")
            % {"count": updated},
        )

    @action(
        description=str(_("Unapprove selected comments")),
        variant=ActionVariant.WARNING,
        icon="cancel",
    )
    @transaction.atomic
    def unapprove_comments(self, request, queryset):
        updated = queryset.update(approved=False)
        self.message_user(
            request,
            _("%(count)d comments were successfully unapproved.")
            % {"count": updated},
        )

    @action(
        description=str(_("Mark as spam and delete")),
        variant=ActionVariant.DANGER,
        icon="report",
    )
    def mark_as_spam(self, request, queryset):
        count = queryset.count()
        queryset.delete()
        self.message_user(
            request,
            _("%(count)d comments were marked as spam and deleted.")
            % {"count": count},
        )
