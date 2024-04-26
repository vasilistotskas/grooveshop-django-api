from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from mptt.admin import DraggableMPTTAdmin
from parler.admin import TranslatableAdmin

from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.comment import BlogComment
from blog.models.post import BlogPost
from blog.models.tag import BlogTag


@admin.register(BlogAuthor)
class BlogAuthorAdmin(TranslatableAdmin):
    search_fields = (
        "user__email",
        "user__username",
        "user__first_name",
        "user__last_name",
    )


@admin.register(BlogTag)
class BlogTagAdmin(TranslatableAdmin):
    list_filter = ("active",)
    list_display = ("name", "active")
    list_editable = ("active",)
    search_fields = ("translations__name",)


@admin.register(BlogCategory)
class BlogCategoryAdmin(TranslatableAdmin, DraggableMPTTAdmin):
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

    setattr(
        related_posts_count,
        "short_description",
        _("Related posts (for this specific category)"),
    )

    def related_posts_cumulative_count(self, instance):
        return instance.posts_cumulative_count

    setattr(
        related_posts_cumulative_count,
        "short_description",
        _("Related posts (in tree)"),
    )


@admin.register(BlogPost)
class BlogPostAdmin(TranslatableAdmin):
    list_display = (
        "id",
        "title",
        "subtitle",
        "slug",
        "published_at",
        "is_published",
    )
    list_filter = (
        "is_published",
        "published_at",
    )
    list_editable = (
        "slug",
        "is_published",
    )
    search_fields = (
        "translations__title",
        "translations__subtitle",
        "slug",
        "translations__body",
    )

    def get_prepopulated_fields(self, request, obj=None):
        # can't use `prepopulated_fields = ..` because it breaks the admin validation
        # for translated fields. This is the official django-parler workaround.
        return {
            "slug": (
                "title",
                "subtitle",
            ),
        }

    date_hierarchy = "published_at"
    save_on_top = True


@admin.register(BlogComment)
class BlogCommentAdmin(TranslatableAdmin, DraggableMPTTAdmin):
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
