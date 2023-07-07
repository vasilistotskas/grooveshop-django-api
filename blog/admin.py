from blog.models.author import BlogAuthor
from blog.models.category import BlogCategory
from blog.models.comment import BlogComment
from blog.models.post import BlogPost
from blog.models.tag import BlogTag
from django.contrib import admin


@admin.register(BlogAuthor)
class BlogAuthorAdmin(admin.ModelAdmin):
    model = BlogAuthor


@admin.register(BlogTag)
class BlogTagAdmin(admin.ModelAdmin):
    model = BlogTag


@admin.register(BlogCategory)
class BlogCategoryAdmin(admin.ModelAdmin):
    model = BlogCategory


@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    model = BlogPost

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
        "title",
        "subtitle",
        "slug",
        "published_at",
        "is_published",
    )
    search_fields = (
        "title",
        "subtitle",
        "slug",
        "body",
    )
    prepopulated_fields = {
        "slug": (
            "title",
            "subtitle",
        )
    }
    date_hierarchy = "published_at"
    save_on_top = True


@admin.register(BlogComment)
class BlogCommentAdmin(admin.ModelAdmin):
    model = BlogComment
    date_hierarchy = "created_at"
