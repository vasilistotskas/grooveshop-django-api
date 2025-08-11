from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from tag.views.tag import TagViewSet
from tag.views.tagged_item import TaggedItemViewSet

urlpatterns = [
    path(
        "tag",
        TagViewSet.as_view({"get": "list", "post": "create"}),
        name="tag-list",
    ),
    path(
        "tag/<int:pk>",
        TagViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="tag-detail",
    ),
    path(
        "tagged-item",
        TaggedItemViewSet.as_view({"get": "list", "post": "create"}),
        name="tagged-item-list",
    ),
    path(
        "tagged-item/<int:pk>",
        TaggedItemViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="tagged-item-detail",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)
