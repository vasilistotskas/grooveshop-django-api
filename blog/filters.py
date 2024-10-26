from django_filters import rest_framework as filters

from blog.models import BlogPost


class BlogPostFilter(filters.FilterSet):
    title = filters.CharFilter(field_name="translations__title", lookup_expr="icontains")
    author_email = filters.CharFilter(field_name="author__user__email", lookup_expr="icontains")

    class Meta:
        model = BlogPost
        fields = ["id", "tags", "slug", "author", "title", "author_email"]
