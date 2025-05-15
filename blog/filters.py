from django_filters import rest_framework as filters

from blog.models import BlogPost


class BlogPostFilter(filters.FilterSet):
    title = filters.CharFilter(
        field_name="translations__title", lookup_expr="icontains"
    )
    author_email = filters.CharFilter(
        field_name="author__user__email", lookup_expr="icontains"
    )
    min_likes = filters.NumberFilter(
        field_name="likes_count_field",
        lookup_expr="gte",
        label="Minimum Likes Count",
    )
    min_comments = filters.NumberFilter(
        field_name="comments_count_field",
        lookup_expr="gte",
        label="Minimum Comments Count",
    )
    min_tags = filters.NumberFilter(
        field_name="tags_count_field",
        lookup_expr="gte",
        label="Minimum Tags Count",
    )
    featured = filters.BooleanFilter(field_name="featured", lookup_expr="exact")
    min_view_count = filters.NumberFilter(
        field_name="view_count", lookup_expr="gte", label="Minimum View Count"
    )

    class Meta:
        model = BlogPost
        fields = [
            "id",
            "tags",
            "slug",
            "author",
            "title",
            "author_email",
            "featured",
            "min_likes",
            "min_comments",
            "min_tags",
            "min_view_count",
            "category",
        ]
