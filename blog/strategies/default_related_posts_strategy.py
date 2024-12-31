from blog.models.post import BlogPost
from blog.strategies.related_posts_strategy import RelatedPostsStrategy


class DefaultRelatedPostsStrategy(RelatedPostsStrategy):
    def get_related_posts(self, post: BlogPost):
        return (
            BlogPost.objects.filter(category=post.category)
            .exclude(pk=post.pk)
            .order_by("-published_at")
        )
