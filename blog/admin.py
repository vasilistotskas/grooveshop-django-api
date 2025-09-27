from django.contrib import admin
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.html import format_html
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
from unfold.contrib.forms.widgets import WysiwygWidget
from unfold.decorators import action
from unfold.enums import ActionVariant

from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.comment import BlogComment
from blog.models.post import BlogPost, BlogPostTranslation
from blog.models.tag import BlogTag


class LikesCountFilter(RangeNumericListFilter):
    title = _("Likes")
    parameter_name = "likes_count"

    def queryset(self, request, queryset):
        filters = {}

        queryset = queryset.annotate(
            likes_count_annotation=Count("likes", distinct=True)
        )

        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        if value_from and value_from != "":
            filters["likes_count_annotation__gte"] = value_from

        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to and value_to != "":
            filters["likes_count_annotation__lte"] = value_to

        return queryset.filter(**filters) if filters else queryset

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class CommentsCountFilter(RangeNumericListFilter):
    title = _("Comments")
    parameter_name = "comments_count"

    def queryset(self, request, queryset):
        filters = {}

        queryset = queryset.annotate(
            comments_count_annotation=Count(
                "comments", distinct=True, filter=Q(comments__approved=True)
            )
        )

        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        if value_from and value_from != "":
            filters["comments_count_annotation__gte"] = value_from

        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to and value_to != "":
            filters["comments_count_annotation__lte"] = value_to

        return queryset.filter(**filters) if filters else queryset

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class TagsCountFilter(RangeNumericListFilter):
    title = _("Tags")
    parameter_name = "tags_count"

    def queryset(self, request, queryset):
        filters = {}

        queryset = queryset.annotate(
            tags_count_annotation=Count(
                "tags", distinct=True, filter=Q(tags__active=True)
            )
        )

        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        if value_from and value_from != "":
            filters["tags_count_annotation__gte"] = value_from

        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to and value_to != "":
            filters["tags_count_annotation__lte"] = value_to

        return queryset.filter(**filters) if filters else queryset

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class PostsCountFilter(RangeNumericListFilter):
    title = _("Posts")
    parameter_name = "posts_count"

    def queryset(self, request, queryset):
        filters = {}

        if hasattr(queryset.model, "blog_posts"):
            queryset = queryset.annotate(
                posts_count_annotation=Count("blog_posts", distinct=True)
            )
        else:
            queryset = queryset.annotate(
                posts_count_annotation=Count("posts", distinct=True)
            )

        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        if value_from and value_from != "":
            filters["posts_count_annotation__gte"] = value_from

        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to and value_to != "":
            filters["posts_count_annotation__lte"] = value_to

        return queryset.filter(**filters) if filters else queryset

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

    def content_preview(self, obj):
        content = (
            obj.safe_translation_getter("content", any_language=True) or ""
        )
        return content[:50] + "..." if len(content) > 50 else content

    content_preview.short_description = _("Content Preview")


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

    def user_display(self, obj):
        return format_html(
            '<div class="flex items-center gap-2">'
            '<strong class="text-base-900 dark:text-base-100">{}</strong>'
            '<span class="text-base-500 dark:text-base-400">({}</span>'
            "</div>",
            obj.user.full_name or obj.user.username,
            obj.user.email,
        )

    user_display.short_description = _("User")

    def bio_preview(self, obj):
        bio = obj.safe_translation_getter("bio", any_language=True) or ""
        if len(bio) > 50:
            return f"{bio[:50]}..."
        return bio

    bio_preview.short_description = _("Bio")

    def posts_count(self, obj):
        count = obj.blog_posts.count()
        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">{}</span>',
            count,
        )

    posts_count.short_description = _("Posts")

    def total_likes_display(self, obj):
        total = obj.total_likes_received
        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full gap-1">'
            "<span>❤️</span>"
            "<span>{}</span>"
            "</span>",
            total,
        )

    total_likes_display.short_description = _("Total Likes")

    def website_link(self, obj):
        if obj.website:
            return format_html(
                '<a href="{}" target="_blank" class="inline-flex items-center gap-1 text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300">'
                "<span>🔗</span><span>Website</span>"
                "</a>",
                obj.website,
            )
        return "-"

    website_link.short_description = _("Website")


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
    readonly_fields = [
        "sort_order",
    ]

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

    def name_display(self, obj):
        name = (
            obj.safe_translation_getter("name", any_language=True)
            or "Unnamed Tag"
        )
        return format_html(
            '<strong class="text-base-900 dark:text-base-100">{}</strong>', name
        )

    name_display.short_description = _("Name")

    def active_badge(self, obj):
        if obj.active:
            return format_html(
                '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full gap-1">'
                "<span>✓</span>"
                "<span>Active</span>"
                "</span>"
            )
        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full gap-1">'
            "<span>✗</span>"
            "<span>Inactive</span>"
            "</span>"
        )

    active_badge.short_description = _("Status")

    def posts_count_badge(self, obj):
        count = obj.blog_posts.count()
        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">{}</span>',
            count,
        )

    posts_count_badge.short_description = _("Posts")


@admin.register(BlogCategory)
class BlogCategoryAdmin(ModelAdmin, TranslatableAdmin, DraggableMPTTAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = False

    mptt_indent_field = "translations__name"
    list_per_page = 15
    list_display = (
        "tree_actions",
        "indented_title",
        "category_image",
        "posts_count_display",
        "recursive_posts_display",
    )
    list_display_links = ("indented_title",)
    search_fields = ("translations__name", "translations__description")
    readonly_fields = [
        "id",
        "posts_count_display",
        "recursive_posts_display",
        "sort_order",
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

    def category_image(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="max-height: 40px; max-width: 80px; border-radius: 4px;" />',
                obj.image.url,
            )
        return format_html(
            '<span class="text-base-400 dark:text-base-500">No image</span>'
        )

    category_image.short_description = _("Image")

    def posts_count_display(self, instance):
        count = getattr(instance, "posts_count", 0)
        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-semibold bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full">{}</span>',
            count,
        )

    posts_count_display.short_description = _("Direct Posts")

    def recursive_posts_display(self, instance):
        count = getattr(instance, "posts_cumulative_count", 0)
        return format_html(
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-purple-50 dark:bg-purple-900 text-purple-700 dark:text-purple-300 rounded-full">{}</span>',
            count,
        )

    recursive_posts_display.short_description = _("Total Posts (Tree)")


@admin.register(BlogPost)
class BlogPostAdmin(ModelAdmin, TranslatableAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True
    list_horizontal_scrollbar_top = False

    formfield_overrides = {
        models.TextField: {
            "widget": WysiwygWidget,
        },
    }

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
                "fields": ("seo_score",),
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
            .with_likes_count_annotation()
            .with_comments_count_annotation()
            .with_tags_count_annotation()
        )

    def title_display(self, obj):
        title = (
            obj.safe_translation_getter("title", any_language=True)
            or "Untitled"
        )
        return format_html(
            '<strong class="text-base-900 dark:text-base-100">{}</strong>',
            title[:50],
        )

    title_display.short_description = _("Title")

    def category_badge(self, obj):
        if obj.category:
            category_name = (
                obj.category.safe_translation_getter("name", any_language=True)
                or "Unnamed"
            )
            return format_html(
                '<span class="inline-flex items-center px-2 py-1 text-xs font-medium bg-indigo-50 dark:bg-indigo-900 text-indigo-700 dark:text-indigo-300 rounded-full">{}</span>',
                category_name,
            )
        return format_html(
            '<span class="text-base-400 dark:text-base-500">No category</span>'
        )

    category_badge.short_description = _("Category")

    def author_display(self, obj):
        if obj.author:
            author_name = obj.author.user.full_name or obj.author.user.username
            return format_html(
                '<span class="font-medium text-base-700 dark:text-base-300">{}</span>',
                author_name,
            )
        return format_html(
            '<span class="text-base-400 dark:text-base-500">No author</span>'
        )

    author_display.short_description = _("Author")

    def featured_badge(self, obj):
        if obj.featured:
            return format_html(
                '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium bg-yellow-50 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 rounded-full gap-1">'
                "<span>⭐</span>"
                "<span>Featured</span>"
                "</span>"
            )
        return ""

    featured_badge.short_description = _("Featured")

    def publish_status_badge(self, obj):
        if obj.is_published and obj.published_at:
            if obj.published_at <= timezone.now():
                return format_html(
                    '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full gap-1">'
                    "<span>✓</span>"
                    "<span>Published</span>"
                    "</span>"
                )
            else:
                return format_html(
                    '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium bg-blue-50 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded-full gap-1">'
                    "<span>📅</span>"
                    "<span>Scheduled</span>"
                    "</span>"
                )
        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium bg-orange-50 dark:bg-orange-900 text-orange-700 dark:text-orange-300 rounded-full gap-1">'
            "<span>📝</span>"
            "<span>Draft</span>"
            "</span>"
        )

    publish_status_badge.short_description = _("Status")

    def engagement_metrics(self, obj):
        likes = obj.likes_count
        comments = obj.comments_count
        views = obj.view_count

        return format_html(
            '<div class="text-sm text-base-700 dark:text-base-300 flex items-center gap-3">'
            '<span class="flex items-center gap-1 text-red-600 dark:text-red-400">'
            "<span>❤️</span><span>{}</span>"
            "</span>"
            '<span class="flex items-center gap-1 text-blue-600 dark:text-blue-400">'
            "<span>💬</span><span>{}</span>"
            "</span>"
            '<span class="flex items-center gap-1 text-green-600 dark:text-green-400">'
            "<span>👀</span><span>{}</span>"
            "</span>"
            "</div>",
            likes,
            comments,
            views,
        )

    engagement_metrics.short_description = _("Engagement")

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
            '<span class="inline-flex items-center px-2 py-1 text-xs font-medium {} {} rounded-full">{}%</span>',
            bg_class,
            text_class,
            score,
        )

    seo_score.short_description = _("SEO Score")

    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="max-height: 100px; max-width: 80px; border-radius: 4px; object-fit: cover;" />',
                obj.image.url,
            )
        return format_html(
            '<span class="text-base-400 dark:text-base-500">No image</span>'
        )

    image_preview.short_description = ""

    def get_prepopulated_fields(self, request, obj=None):
        return {
            "slug": ("title",),
        }

    @action(
        description=_("Mark selected posts as featured"),
        variant=ActionVariant.PRIMARY,
        icon="star",
    )
    def mark_as_featured(self, request, queryset):
        updated = queryset.update(featured=True)
        self.message_user(
            request,
            _("%(count)d posts were successfully marked as featured.")
            % {"count": updated},
        )

    @action(
        description=_("Remove featured mark from selected posts"),
        variant=ActionVariant.WARNING,
        icon="star_border",
    )
    def unmark_as_featured(self, request, queryset):
        updated = queryset.update(featured=False)
        self.message_user(
            request,
            _("%(count)d posts were successfully unmarked as featured.")
            % {"count": updated},
        )

    @action(
        description=_("Publish selected posts"),
        variant=ActionVariant.SUCCESS,
        icon="publish",
    )
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
        description=_("Unpublish selected posts"),
        variant=ActionVariant.WARNING,
        icon="unpublished",
    )
    def unpublish_posts(self, request, queryset):
        updated = queryset.update(is_published=False)
        self.message_user(
            request,
            _("%(count)d posts were successfully unpublished.")
            % {"count": updated},
        )

    @action(
        description=_("Increment view count by 100"),
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
        description=_("Reset view count to zero"),
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
        return (
            super()
            .get_queryset(request)
            .select_related("post", "user", "parent")
        )

    def user_display(self, obj):
        if obj.user:
            full_name = obj.user.full_name
            username = obj.user.username
            email = obj.user.email

            display_name = full_name if full_name else username
            return format_html(
                '<div class="text-sm">'
                '<div class="font-medium text-base-900 dark:text-base-100">{}</div>'
                '<div class="text-base-500 dark:text-base-400">{}</div>'
                "</div>",
                display_name,
                email,
            )
        return format_html(
            '<span class="text-base-400 dark:text-base-500 italic">Anonymous</span>'
        )

    user_display.short_description = _("User")

    def post_link(self, obj):
        if obj.post:
            title = (
                obj.post.safe_translation_getter("title", any_language=True)
                or f"Post {obj.post.id}"
            )
            url = f"/admin/blog/blogpost/{obj.post.id}/change/"
            return format_html(
                '<a href="{}" class="text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 font-medium">{}</a>',
                url,
                title[:30] + "..." if len(title) > 30 else title,
            )
        return format_html(
            '<span class="text-base-400 dark:text-base-500">No post</span>'
        )

    post_link.short_description = _("Post")

    def approval_badge(self, obj):
        if obj.approved:
            return format_html(
                '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium bg-green-50 dark:bg-green-900 text-green-700 dark:text-green-300 rounded-full gap-1">'
                "<span>✓</span>"
                "<span>Approved</span>"
                "</span>"
            )
        return format_html(
            '<span class="inline-flex items-center justify-center px-2 py-1 text-xs font-medium bg-red-50 dark:bg-red-900 text-red-700 dark:text-red-300 rounded-full gap-1">'
            "<span>⏳</span>"
            "<span>Pending</span>"
            "</span>"
        )

    approval_badge.short_description = _("Status")

    def engagement_display(self, obj):
        likes = obj.likes_count
        replies = obj.replies_count

        return format_html(
            '<div class="text-sm text-base-700 dark:text-base-300 flex items-center gap-3">'
            '<span class="flex items-center gap-1 text-red-600 dark:text-red-400">'
            "<span>❤️</span><span>{}</span>"
            "</span>"
            '<span class="flex items-center gap-1 text-blue-600 dark:text-blue-400">'
            "<span>💬</span><span>{}</span>"
            "</span>"
            "</div>",
            likes,
            replies,
        )

    engagement_display.short_description = _("Engagement")

    @action(
        description=_("Approve selected comments"),
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def approve_comments(self, request, queryset):
        updated = queryset.update(approved=True)
        self.message_user(
            request,
            _("%(count)d comments were successfully approved.")
            % {"count": updated},
        )

    @action(
        description=_("Unapprove selected comments"),
        variant=ActionVariant.WARNING,
        icon="cancel",
    )
    def unapprove_comments(self, request, queryset):
        updated = queryset.update(approved=False)
        self.message_user(
            request,
            _("%(count)d comments were successfully unapproved.")
            % {"count": updated},
        )

    @action(
        description=_("Mark as spam and delete"),
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
