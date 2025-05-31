from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from mptt.admin import DraggableMPTTAdmin
from parler.admin import TranslatableAdmin
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import (
    RangeDateTimeFilter,
    RangeNumericListFilter,
    RelatedDropdownFilter,
    SliderNumericFilter,
)
from unfold.decorators import action
from unfold.enums import ActionVariant

from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.comment import BlogComment
from blog.models.post import BlogPost
from blog.models.tag import BlogTag


class LikesCountFilter(RangeNumericListFilter):
    title = _("Likes count")
    parameter_name = "likes_count"

    def queryset(self, request, queryset):
        filters = {}

        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        if value_from and value_from != "":
            filters["likes_count_field__gte"] = value_from

        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to and value_to != "":
            filters["likes_count_field__lte"] = value_to

        return queryset.filter(**filters) if filters else queryset

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class CommentsCountFilter(RangeNumericListFilter):
    title = _("Comments count")
    parameter_name = "comments_count"

    def queryset(self, request, queryset):
        filters = {}

        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        if value_from and value_from != "":
            filters["comments_count_field__gte"] = value_from

        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to and value_to != "":
            filters["comments_count_field__lte"] = value_to

        return queryset.filter(**filters) if filters else queryset

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class TagsCountFilter(RangeNumericListFilter):
    title = _("Tags count")
    parameter_name = "tags_count"

    def queryset(self, request, queryset):
        filters = {}

        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        if value_from and value_from != "":
            filters["tags_count_field__gte"] = value_from

        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to and value_to != "":
            filters["tags_count_field__lte"] = value_to

        return queryset.filter(**filters) if filters else queryset

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class PostsCountFilter(RangeNumericListFilter):
    title = _("Posts count")
    parameter_name = "posts_count"

    def queryset(self, request, queryset):
        filters = {}

        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        if value_from and value_from != "":
            filters["posts_count_field__gte"] = value_from

        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to and value_to != "":
            filters["posts_count_field__lte"] = value_to

        return queryset.filter(**filters) if filters else queryset

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


@admin.register(BlogAuthor)
class BlogAuthorAdmin(ModelAdmin, TranslatableAdmin):
    list_display = ("id", "user", "bio_preview", "posts_count")
    search_fields = (
        "user__email",
        "user__username",
        "user__first_name",
        "user__last_name",
    )
    list_select_related = ["user"]

    def bio_preview(self, obj):
        bio = obj.safe_translation_getter("bio", any_language=True) or ""
        if len(bio) > 50:
            return f"{bio[:50]}..."
        return bio

    bio_preview.short_description = _("Bio")

    def posts_count(self, obj):
        return obj.blog_posts.count()

    posts_count.short_description = _("Posts Count")


@admin.register(BlogTag)
class BlogTagAdmin(ModelAdmin, TranslatableAdmin):
    list_display = ("name", "active", "posts_count")
    list_filter = ("active", PostsCountFilter)
    list_editable = ("active",)
    search_fields = ("translations__name",)

    def posts_count(self, obj):
        return obj.blog_posts.count()

    posts_count.short_description = _("Posts Count")


@admin.register(BlogCategory)
class BlogCategoryAdmin(ModelAdmin, TranslatableAdmin, DraggableMPTTAdmin):
    mptt_indent_field = "translations__name"
    list_per_page = 10
    list_display = (
        "id",
        "sort_order",
        "tree_actions",
        "indented_title",
        "recursive_post_count",
        "related_posts_cumulative_count",
    )
    list_display_links = ("indented_title",)
    search_fields = ("translations__name",)

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

    def related_posts_count(self, instance):
        return instance.posts_count

    related_posts_count.short_description = _(
        "Related posts (for this specific category)"
    )

    def related_posts_cumulative_count(self, instance):
        return instance.posts_cumulative_count

    related_posts_cumulative_count.short_description = _(
        "Related posts (in tree)"
    )


@admin.register(BlogPost)
class BlogPostAdmin(ModelAdmin, TranslatableAdmin):
    list_display = (
        "id",
        "title",
        "category",
        "author",
        "featured",
        "view_count",
        "likes_count_display",
        "comments_count_display",
        "tags_count_display",
        "published_at",
        "is_published",
        "image_preview",
    )
    list_filter = (
        "featured",
        "is_published",
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
        "view_count",
        "created_at",
        "updated_at",
        "id",
        "likes_count_display",
        "comments_count_display",
        "tags_count_display",
    ]
    filter_horizontal = ["tags"]
    actions = [
        "mark_as_featured",
        "unmark_as_featured",
        "increment_view_count",
        "reset_view_count",
    ]
    date_hierarchy = "published_at"
    save_on_top = True
    list_filter_submit = True
    list_filter_sheet = True
    list_per_page = 25
    list_select_related = ["category", "author"]

    def get_queryset(self, request):
        return super().get_queryset(request).with_all_annotations()

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

    def likes_count_display(self, obj):
        return obj.likes_count

    likes_count_display.short_description = _("Likes Count")
    likes_count_display.admin_order_field = "likes_count_field"

    def comments_count_display(self, obj):
        return obj.comments_count

    comments_count_display.short_description = _("Comments Count")
    comments_count_display.admin_order_field = "comments_count_field"

    def tags_count_display(self, obj):
        return obj.tags_count

    tags_count_display.short_description = _("Tags Count")
    tags_count_display.admin_order_field = "tags_count_field"

    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="max-height: 30px; max-width: 60px;" />',
                obj.image.url,
            )
        return "-"

    image_preview.short_description = ""

    def image_tag(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="max-height: 200px; max-width: 400px;" />',
                obj.image.url,
            )
        return "-"

    image_tag.short_description = _("Image")

    def get_prepopulated_fields(self, request, obj=None):
        return {
            "slug": ("title",),
        }


@admin.register(BlogComment)
class BlogCommentAdmin(ModelAdmin, TranslatableAdmin, DraggableMPTTAdmin):
    list_filter_submit = True
    mptt_indent_field = "user"
    list_per_page = 20
    list_display = (
        "tree_actions",
        "indented_title",
        "comment_preview",
        "user_display",
        "post_link",
        "likes_count_display",
        "replies_count_display",
        "created_at",
        "is_approved",
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
        "is_approved",
        ("created_at", RangeDateTimeFilter),
        ("post", RelatedDropdownFilter),
        ("user", RelatedDropdownFilter),
    )
    list_select_related = ["post", "user", "parent"]
    actions = [
        "approve_comments",
        "unapprove_comments",
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("post", "user")

    def comment_preview(self, obj):
        content = (
            obj.safe_translation_getter("content", any_language=True) or ""
        )
        if len(content) > 50:
            return f"{content[:50]}..."
        return content

    comment_preview.short_description = _("Comment")

    def user_display(self, obj):
        if obj.user:
            return f"{obj.user.username} ({obj.user.email})"
        return _("Anonymous")

    user_display.short_description = _("User")

    def post_link(self, obj):
        if obj.post:
            title = (
                obj.post.safe_translation_getter("title", any_language=True)
                or f"Post {obj.post.id}"
            )
            url = f"/admin/blog/blogpost/{obj.post.id}/change/"
            return format_html('<a href="{}">{}</a>', url, title)
        return "-"

    post_link.short_description = _("Post")

    def likes_count_display(self, obj):
        return obj.likes_count

    likes_count_display.short_description = _("Likes Count")

    def replies_count_display(self, obj):
        return obj.replies_count

    replies_count_display.short_description = _("Replies Count")

    @action(
        description=_("Approve selected comments"),
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def approve_comments(self, request, queryset):
        updated = queryset.update(is_approved=True)
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
        updated = queryset.update(is_approved=False)
        self.message_user(
            request,
            _("%(count)d comments were successfully unapproved.")
            % {"count": updated},
        )
