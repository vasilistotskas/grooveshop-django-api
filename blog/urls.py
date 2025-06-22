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
        "blog/post/trending",
        BlogPostViewSet.as_view({"get": "trending"}),
        name="blog-post-trending",
    ),
    path(
        "blog/post/popular",
        BlogPostViewSet.as_view({"get": "popular"}),
        name="blog-post-popular",
    ),
    path(
        "blog/post/featured",
        BlogPostViewSet.as_view({"get": "featured"}),
        name="blog-post-featured",
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
        "blog/post/<int:pk>/related_posts",
        BlogPostViewSet.as_view({"get": "related_posts"}),
        name="blog-post-related_posts",
    ),
    path(
        "blog/category",
        BlogCategoryViewSet.as_view({"get": "list", "post": "create"}),
        name="blog-category-list",
    ),
    path(
        "blog/category/tree",
        BlogCategoryViewSet.as_view({"get": "tree"}),
        name="blog-category-tree",
    ),
    path(
        "blog/category/reorder",
        BlogCategoryViewSet.as_view({"post": "reorder"}),
        name="blog-category-reorder",
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
        "blog/category/<int:pk>/children",
        BlogCategoryViewSet.as_view({"get": "children"}),
        name="blog-category-children",
    ),
    path(
        "blog/category/<int:pk>/descendants",
        BlogCategoryViewSet.as_view({"get": "descendants"}),
        name="blog-category-descendants",
    ),
    path(
        "blog/category/<int:pk>/ancestors",
        BlogCategoryViewSet.as_view({"get": "ancestors"}),
        name="blog-category-ancestors",
    ),
    path(
        "blog/category/<int:pk>/siblings",
        BlogCategoryViewSet.as_view({"get": "siblings"}),
        name="blog-category-siblings",
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
        "blog/author/<int:pk>/posts",
        BlogAuthorViewSet.as_view({"get": "posts"}),
        name="blog-author-posts",
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
        "blog/comment/<int:pk>/replies",
        BlogCommentViewSet.as_view({"get": "replies"}),
        name="blog-comment-replies",
    ),
    path(
        "blog/comment/<int:pk>/thread",
        BlogCommentViewSet.as_view({"get": "thread"}),
        name="blog-comment-thread",
    ),
    path(
        "blog/comment/<int:pk>/update_likes",
        BlogCommentViewSet.as_view({"post": "update_likes"}),
        name="blog-comment-update_likes",
    ),
    path(
        "blog/comment/<int:pk>/post",
        BlogCommentViewSet.as_view({"get": "post"}),
        name="blog-comment-post",
    ),
    path(
        "blog/comment/liked_comments",
        BlogCommentViewSet.as_view({"post": "liked_comments"}),
        name="blog-comment-liked_comments",
    ),
    path(
        "blog/comment/my_comments",
        BlogCommentViewSet.as_view({"get": "my_comments"}),
        name="blog-comment-my-comments",
    ),
    path(
        "blog/comment/my_comment",
        BlogCommentViewSet.as_view({"post": "my_comment"}),
        name="blog-comment-my-comment",
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
