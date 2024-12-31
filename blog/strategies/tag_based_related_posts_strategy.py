from blog.models.post import BlogPost
from blog.strategies.related_posts_strategy import RelatedPostsStrategy


class TagBasedRelatedPostsStrategy(RelatedPostsStrategy):
    def get_related_posts(self, post: BlogPost):
        return (
            BlogPost.objects.filter(tags__in=post.tags.all())
            .exclude(pk=post.pk)
            .distinct()
            .order_by("-published_at")
        )
