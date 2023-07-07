from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from blog.views.author import BlogAuthorViewSet
from blog.views.category import BlogCategoryViewSet
from blog.views.comment import BlogCommentViewSet
from blog.views.post import BlogPostViewSet
from blog.views.tag import BlogTagViewSet

urlpatterns = [
    path("blog/post/", BlogPostViewSet.as_view({"get": "list", "post": "create"})),
    path(
        "blog/post/<int:pk>/",
        BlogPostViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
    ),
    path(
        "blog/post/<int:pk>/update_likes/",
        BlogPostViewSet.as_view({"post": "update_likes"}),
    ),
    path(
        "blog/post/<int:pk>/update_view_count/",
        BlogPostViewSet.as_view({"post": "update_view_count"}),
    ),
    path(
        "blog/category/",
        BlogCategoryViewSet.as_view({"get": "list", "post": "create"}),
    ),
    path(
        "blog/category/<int:pk>/",
        BlogCategoryViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
    ),
    path("blog/author/", BlogAuthorViewSet.as_view({"get": "list", "post": "create"})),
    path(
        "blog/author/<int:pk>/",
        BlogAuthorViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
    ),
    path(
        "blog/comment/", BlogCommentViewSet.as_view({"get": "list", "post": "create"})
    ),
    path(
        "blog/comment/<int:pk>/",
        BlogCommentViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
    ),
    path("blog/tag/", BlogTagViewSet.as_view({"get": "list", "post": "create"})),
    path(
        "blog/tag/<int:pk>/",
        BlogTagViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
