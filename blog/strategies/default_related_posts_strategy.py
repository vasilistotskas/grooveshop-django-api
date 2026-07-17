from blog.models.post import BlogPost
from blog.strategies.related_posts_strategy import RelatedPostsStrategy


class DefaultRelatedPostsStrategy(RelatedPostsStrategy):
    def get_related_posts(self, post: BlogPost):
        # Only surface published posts — related posts are public and must not
        # leak drafts / future-dated posts.
        return (
            BlogPost.objects.filter(category=post.category)
            .published()
            .exclude(pk=post.pk)
            .order_by("-published_at")
        )
