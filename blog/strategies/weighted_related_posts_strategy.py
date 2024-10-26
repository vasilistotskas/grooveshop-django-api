from django.db.models import QuerySet

from blog.models.post import BlogPost
from blog.strategies.related_posts_strategy import RelatedPostsStrategy


class WeightedRelatedPostsStrategy(RelatedPostsStrategy):
    """
    Aggregator strategy that combines multiple strategies based on assigned weights.
    """

    def __init__(self, strategies_with_weights: list[tuple[RelatedPostsStrategy, float]], limit: int = 8):
        """
        Initialize with a list of strategies and their respective weights.

        :param strategies_with_weights: List of tuples containing (strategy_instance, weight).
        :param limit: Total number of related posts to retrieve.
        """
        self.strategies_with_weights = strategies_with_weights
        self.limit = limit

    def get_related_posts(self, post: BlogPost) -> QuerySet[BlogPost]:
        collected_posts = []
        collected_post_ids = set()
        remaining_limit = self.limit

        for strategy, weight in self.strategies_with_weights:
            num_posts = int(weight * self.limit)
            num_posts = min(num_posts, remaining_limit)
            if num_posts <= 0:
                continue

            posts = strategy.get_related_posts(post).exclude(pk__in=collected_post_ids)[:num_posts]
            collected_posts.extend(posts)
            collected_post_ids.update(posts.values_list("pk", flat=True))
            remaining_limit -= len(posts)
            if remaining_limit <= 0:
                break

        if remaining_limit > 0:
            for strategy, weight in self.strategies_with_weights:
                posts = strategy.get_related_posts(post).exclude(pk__in=collected_post_ids)[
                    :remaining_limit
                ]
                for p in posts:
                    if p.pk not in collected_post_ids:
                        collected_posts.append(p)
                        collected_post_ids.add(p.pk)
                        remaining_limit -= 1
                        if remaining_limit <= 0:
                            break
                if remaining_limit <= 0:
                    break

        return BlogPost.objects.filter(pk__in=[p.pk for p in collected_posts]).order_by("-published_at")
