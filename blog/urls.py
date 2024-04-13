from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from blog.views.author import BlogAuthorViewSet
from blog.views.category import BlogCategoryViewSet
from blog.views.comment import BlogCommentViewSet
from blog.views.post import BlogPostViewSet
from blog.views.tag import BlogTagViewSet

urlpatterns = [
    path(
        "blog/post",
        BlogPostViewSet.as_view({"get": "list", "post": "create"}),
        name="blog-post-list",
    ),
    path(
        "blog/post/liked_posts",
        BlogPostViewSet.as_view({"post": "liked_posts"}),
        name="blog-post-liked_posts",
    ),
    path(
        "blog/post/<int:pk>",
        BlogPostViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="blog-post-detail",
    ),
    path(
        "blog/post/<int:pk>/update_likes",
        BlogPostViewSet.as_view({"post": "update_likes"}),
        name="blog-post-update_likes",
    ),
    path(
        "blog/post/<int:pk>/update_view_count",
        BlogPostViewSet.as_view({"post": "update_view_count"}),
        name="blog-post-update_view_count",
    ),
    path(
        "blog/post/<int:pk>/comments",
        BlogPostViewSet.as_view({"get": "comments"}),
        name="blog-post-comments",
    ),
    path(
        "blog/category",
        BlogCategoryViewSet.as_view({"get": "list", "post": "create"}),
        name="blog-category-list",
    ),
    path(
        "blog/category/<int:pk>",
        BlogCategoryViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="blog-category-detail",
    ),
    path(
        "blog/category/<int:pk>/posts",
        BlogCategoryViewSet.as_view({"get": "posts"}),
        name="blog-category-posts",
    ),
    path(
        "blog/author",
        BlogAuthorViewSet.as_view({"get": "list", "post": "create"}),
        name="blog-author-list",
    ),
    path(
        "blog/author/<int:pk>",
        BlogAuthorViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="blog-author-detail",
    ),
    path(
        "blog/comment",
        BlogCommentViewSet.as_view({"get": "list", "post": "create"}),
        name="blog-comment-list",
    ),
    path(
        "blog/comment/<int:pk>",
        BlogCommentViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="blog-comment-detail",
    ),
    path(
        "blog/comment/user_blog_comment",
        BlogCommentViewSet.as_view({"post": "user_blog_comment"}),
        name="blog-comment-user-blog-comment",
    ),
    path(
        "blog/comment/liked_comments",
        BlogCommentViewSet.as_view({"post": "liked_comments"}),
        name="blog-comment-liked_comments",
    ),
    path(
        "blog/comment/<int:pk>/update_likes",
        BlogCommentViewSet.as_view({"post": "update_likes"}),
        name="blog-comment-update_likes",
    ),
    path(
        "blog/comment/<int:pk>/replies",
        BlogCommentViewSet.as_view({"get": "replies"}),
        name="blog-comment-replies",
    ),
    path(
        "blog/comment/<int:pk>/post",
        BlogCommentViewSet.as_view({"get": "post"}),
        name="blog-comment-post",
    ),
    path(
        "blog/tag",
        BlogTagViewSet.as_view({"get": "list", "post": "create"}),
        name="blog-tag-list",
    ),
    path(
        "blog/tag/<int:pk>",
        BlogTagViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="blog-tag-detail",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
