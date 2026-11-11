from blog.models.post import BlogPost
from blog.strategies.related_posts_strategy import RelatedPostsStrategy


class DefaultRelatedPostsStrategy(RelatedPostsStrategy):
    def related_ids(self, post: BlogPost, limit: int) -> list[int]:
        # Only surface published posts — related posts are public and must not
        # leak drafts / future-dated posts.
        return list(
            BlogPost.objects.filter(category=post.category)
            .published()
            .exclude(pk=post.pk)
            .order_by("-published_at")
            .values_list("pk", flat=True)[:limit]
        )
