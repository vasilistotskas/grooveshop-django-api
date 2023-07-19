from django.contrib import admin
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
class BlogCategoryAdmin(TranslatableAdmin):
    search_fields = ("translations__name",)


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
        "published_at",
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
class BlogCommentAdmin(TranslatableAdmin):
    date_hierarchy = "created_at"
    search_fields = (
        "user__email",
        "user__first_name",
        "user__last_name",
        "post__translations__title",
        "post__translations__subtitle",
    )
