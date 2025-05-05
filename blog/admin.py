from typing import override

from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from mptt.admin import DraggableMPTTAdmin
from parler.admin import TranslatableAdmin
from unfold.admin import ModelAdmin
from unfold.decorators import action
from unfold.enums import ActionVariant

from blog.enum.blog_post_enum import PostStatusEnum
from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.comment import BlogComment
from blog.models.post import BlogPost
from blog.models.tag import BlogTag


@admin.register(BlogAuthor)
class BlogAuthorAdmin(ModelAdmin, TranslatableAdmin):
    search_fields = (
        "user__email",
        "user__username",
        "user__first_name",
        "user__last_name",
    )


@admin.register(BlogTag)
class BlogTagAdmin(ModelAdmin, TranslatableAdmin):
    list_filter = ("active",)
    list_display = ("name", "active")
    list_editable = ("active",)
    search_fields = ("translations__name",)


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

    @override
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = BlogCategory.objects.add_related_count(
            qs, BlogPost, "category", "posts_cumulative_count", cumulative=True
        )
        qs = BlogCategory.objects.add_related_count(
            qs, BlogPost, "category", "posts_count", cumulative=False
        )
        return qs

    @override
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
        "subtitle",
        "category",
        "author",
        "status",
        "featured",
        "view_count",
        "likes_count_display",
        "comments_count_display",
        "published_at",
        "is_published",
    )
    list_filter = (
        "is_published",
        "published_at",
        "status",
        "featured",
        "category",
        "author",
        "tags",
    )
    list_editable = (
        "featured",
        "status",
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
    readonly_fields = ["view_count", "created_at", "updated_at", "id"]
    filter_horizontal = ["tags"]
    actions = [
        "make_published",
        "make_draft",
        "mark_as_featured",
        "unmark_as_featured",
    ]
    date_hierarchy = "published_at"
    save_on_top = True
    list_filter_submit = True

    @action(
        description=_("Mark selected posts as published"),
        variant=ActionVariant.SUCCESS,
        icon="publish",
    )
    def make_published(self, request, queryset):
        updated = queryset.update(
            is_published=True, status=PostStatusEnum.PUBLISHED
        )
        self.message_user(
            request,
            _("%(count)d posts were successfully marked as published.")
            % {"count": updated},
        )

    @action(
        description=_("Mark selected posts as draft"),
        variant=ActionVariant.INFO,
        icon="drafts",
    )
    def make_draft(self, request, queryset):
        updated = queryset.update(
            is_published=False, status=PostStatusEnum.DRAFT
        )
        self.message_user(
            request,
            _("%(count)d posts were successfully marked as draft.")
            % {"count": updated},
        )

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

    def likes_count_display(self, obj):
        return obj.likes_count

    likes_count_display.short_description = _("Likes")
    likes_count_display.admin_order_field = "likes__count"

    def comments_count_display(self, obj):
        return obj.comments_count

    comments_count_display.short_description = _("Comments")
    comments_count_display.admin_order_field = "comments__count"

    @override
    def get_prepopulated_fields(self, request, obj=None):
        # can't use `prepopulated_fields = ..` because it breaks the admin validation
        # for translated fields. This is the official django-parler workaround.
        return {
            "slug": ("title",),
        }


@admin.register(BlogComment)
class BlogCommentAdmin(ModelAdmin, TranslatableAdmin, DraggableMPTTAdmin):
    mptt_indent_field = "user"
    list_per_page = 10
    list_display = (
        "tree_actions",
        "indented_title",
        "post",
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
    )
    list_filter = (
        "is_approved",
        "created_at",
    )
