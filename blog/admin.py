from django.contrib import admin
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import TabularInline
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateTimeFilter,
    RangeNumericListFilter,
    RelatedDropdownFilter,
    SliderNumericFilter,
)
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from admin.base import BaseTranslatableAdmin
from admin.displays import header_two_line
from admin.export import ExportActionMixin

from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.comment import BlogComment
from blog.models.post import BlogPost, BlogPostTranslation
from blog.models.tag import BlogTag

# ── Local (single-app) variant maps ────────────────────────────────────
# These states are derived (not backed by a TextChoices field), so they
# can't use ``admin.displays.choice_label`` — each gets a small
# ``@display(label=...)`` method instead.

PUBLISH_STATUS_VARIANT: dict[str, str] = {
    "draft": "warning",
    "scheduled": "info",
    "published": "success",
}

SEO_SCORE_VARIANT: dict[str, str] = {
    "poor": "danger",
    "fair": "warning",
    "good": "success",
}


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
class BlogAuthorAdmin(BaseTranslatableAdmin):
    # Narrower than the base default — this admin's list_display is
    # only 5 plain columns, no benefit from the full-width layout.
    list_fullwidth = False

    list_display = (
        "author_header",
        "bio_preview",
        "posts_count",
        "total_likes_received",
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
    readonly_fields = ["id", "total_likes_received", "posts_count"]

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
                "fields": ("posts_count", "total_likes_received"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(posts_count_ann=Count("blog_posts", distinct=True))
        )

    @display(description=_("User"), header=True, ordering="user__last_name")
    def author_header(self, obj):
        return header_two_line(
            obj.user.full_name or obj.user.username, obj.user.email
        )

    @admin.display(description=_("Bio"))
    def bio_preview(self, obj):
        bio = obj.safe_translation_getter("bio", any_language=True) or ""
        if len(bio) > 50:
            return f"{bio[:50]}..."
        return bio

    @admin.display(description=_("Posts"))
    def posts_count(self, obj):
        return getattr(obj, "posts_count_ann", obj.blog_posts.count())

    @admin.display(description=_("Website"))
    def website_link(self, obj):
        if not obj.website:
            return "—"
        return format_html(
            '<a href="{url}" target="_blank" rel="noopener">{url}</a>',
            url=obj.website,
        )


@admin.register(BlogTag)
class BlogTagAdmin(BaseTranslatableAdmin):
    # Narrower than the base default — 4 plain columns.
    list_fullwidth = False

    list_display = (
        "name_display",
        "active",
        "posts_count",
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

    @admin.display(description=_("Name"), ordering="translations__name")
    def name_display(self, obj):
        return obj.safe_translation_getter("name", any_language=True) or _(
            "Unnamed Tag"
        )

    @admin.display(description=_("Posts"))
    def posts_count(self, obj):
        return getattr(obj, "posts_count_ann", obj.blog_posts.count())


@admin.register(BlogCategory)
class BlogCategoryAdmin(BaseTranslatableAdmin):
    list_filter_sheet = False

    ordering_field = "sort_order"
    hide_ordering_field = True
    list_per_page = 15
    list_display = (
        "category_name",
        "parent_display",
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
        if not obj.parent_id:
            return "—"
        return (
            obj.parent.safe_translation_getter("name", any_language=True)
            or f"#{obj.parent_id}"
        )

    @admin.display(description=_("Direct Posts"))
    def posts_count_display(self, obj):
        return getattr(obj, "posts_count", 0)

    @admin.display(description=_("Total Posts (Tree)"))
    def recursive_posts_display(self, obj):
        return getattr(obj, "posts_cumulative_count", 0)


@admin.register(BlogPost)
class BlogPostAdmin(ExportActionMixin, BaseTranslatableAdmin):
    list_horizontal_scrollbar_top = False

    list_display = (
        "title_display",
        "category_display",
        "author_header",
        "featured",
        "publish_status_label",
        "engagement_display",
        "published_at",
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
    list_editable = ("featured",)
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
        "engagement_display",
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
                "fields": ("view_count", "engagement_display"),
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

    @admin.display(description=_("Title"), ordering="translations__title")
    def title_display(self, obj):
        return obj.safe_translation_getter("title", any_language=True) or _(
            "Untitled"
        )

    @admin.display(description=_("Category"))
    def category_display(self, obj):
        if not obj.category:
            return "—"
        return (
            obj.category.safe_translation_getter("name", any_language=True)
            or "—"
        )

    @display(description=_("Author"), header=True)
    def author_header(self, obj):
        if not obj.author:
            return header_two_line(str(_("No author")))
        user = obj.author.user
        return header_two_line(user.full_name or user.username, user.email)

    @display(description=_("Status"), label=PUBLISH_STATUS_VARIANT)
    def publish_status_label(self, obj):
        if not obj.is_published:
            return "draft", _("Draft")
        if obj.published_at and obj.published_at > timezone.now():
            return "scheduled", _("Scheduled")
        return "published", _("Published")

    @admin.display(description=_("Engagement"))
    def engagement_display(self, obj):
        return _("%(likes)d likes, %(comments)d comments, %(views)d views") % {
            "likes": obj.likes_count,
            "comments": obj.comments_count,
            "views": obj.view_count,
        }

    @display(description=_("SEO Score"), label=SEO_SCORE_VARIANT)
    def seo_score(self, obj):
        score = 0
        title = obj.safe_translation_getter("title", any_language=True) or ""
        subtitle = (
            obj.safe_translation_getter("subtitle", any_language=True) or ""
        )

        if 10 < len(title) < 60:
            score += 25
        if subtitle:
            score += 25
        if obj.image:
            score += 25
        if obj.tags.exists():
            score += 25

        if score >= 75:
            return "good", f"{score}%"
        if score >= 50:
            return "fair", f"{score}%"
        return "poor", f"{score}%"

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
class BlogCommentAdmin(BaseTranslatableAdmin):
    list_per_page = 30
    list_display = (
        "content_preview",
        "user_header",
        "post_link",
        "approved",
        "engagement_display",
        "created_at",
    )
    list_display_links = ("content_preview",)
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
    # Nested comments read top-to-bottom in tree order so the
    # indentation in ``content_preview`` reflects the reply structure.
    ordering = ("tree_id", "lft")
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

    @admin.display(description=_("Content"))
    def content_preview(self, obj):
        content = (
            obj.safe_translation_getter("content", any_language=True) or ""
        )
        preview = content[:50] + "..." if len(content) > 50 else content
        indent = " " * (obj.level * 4)
        return f"{indent}{preview}"

    @display(description=_("User"), header=True)
    def user_header(self, obj):
        if not obj.user:
            return header_two_line(str(_("Anonymous")))
        return header_two_line(
            obj.user.full_name or obj.user.username, obj.user.email
        )

    @admin.display(description=_("Post"))
    def post_link(self, obj):
        if not obj.post:
            return "—"
        title = (
            obj.post.safe_translation_getter("title", any_language=True)
            or f"Post {obj.post.id}"
        )
        title_display = title[:30] + "..." if len(title) > 30 else title
        return format_html(
            '<a href="{url}">{title}</a>',
            url=f"/admin/blog/blogpost/{obj.post.id}/change/",
            title=title_display,
        )

    @admin.display(description=_("Engagement"))
    def engagement_display(self, obj):
        return _("%(likes)d likes, %(replies)d replies") % {
            "likes": obj.likes_count,
            "replies": obj.replies_count,
        }

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
