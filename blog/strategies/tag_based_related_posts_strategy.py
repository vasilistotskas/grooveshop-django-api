from blog.models.post import BlogPost
from blog.strategies.related_posts_strategy import RelatedPostsStrategy


class TagBasedRelatedPostsStrategy(RelatedPostsStrategy):
    def get_related_posts(self, post: BlogPost):
        # Only surface published posts — related posts are public.
        return (
            BlogPost.objects.filter(tags__in=post.tags.all())
            .published()
            .exclude(pk=post.pk)
            .distinct()
            .order_by("-published_at")
        )
