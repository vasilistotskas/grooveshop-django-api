from blog.models.post import BlogPost
from blog.strategies.related_posts_strategy import RelatedPostsStrategy


class TagBasedRelatedPostsStrategy(RelatedPostsStrategy):
    def related_ids(self, post: BlogPost, limit: int) -> list[int]:
        # Only surface published posts — related posts are public.
        return list(
            BlogPost.objects.filter(tags__in=post.tags.all())
            .published()
            .exclude(pk=post.pk)
            .distinct()
            .order_by("-published_at")
            .values_list("pk", flat=True)[:limit]
        )
