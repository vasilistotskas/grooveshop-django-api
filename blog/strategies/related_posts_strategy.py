from django.db.models import QuerySet

from blog.models.post import BlogPost


class RelatedPostsStrategy:
    def get_related_posts(self, post: BlogPost) -> QuerySet[BlogPost]:
        raise NotImplementedError("Subclasses must implement this method.")
