from typing import List
from typing import Tuple

from django.db.models import QuerySet

from blog.models.post import BlogPost
from blog.strategies.related_posts_strategy import RelatedPostsStrategy


class WeightedRelatedPostsStrategy(RelatedPostsStrategy):
    """
    Aggregator strategy that combines multiple strategies based on assigned weights.
    """

    def __init__(self, strategies_with_weights: List[Tuple[RelatedPostsStrategy, float]], limit: int = 8):
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

        for strategy, weight in self.strategies_with_weights:
            num_posts = int(round(weight * self.limit))
            remaining_slots = self.limit - len(collected_posts)
            num_posts = min(num_posts, remaining_slots)
            if num_posts <= 0:
                continue

            posts = strategy.get_related_posts(post).exclude(pk__in=collected_post_ids)[:num_posts]

            for p in posts:
                if p.pk not in collected_post_ids:
                    collected_posts.append(p)
                    collected_post_ids.add(p.pk)
                if len(collected_posts) >= self.limit:
                    break

            if len(collected_posts) >= self.limit:
                break

        if len(collected_posts) < self.limit:
            remaining = self.limit - len(collected_posts)
            fallback_posts = (
                BlogPost.objects.exclude(pk=post.pk)
                .exclude(pk__in=collected_post_ids)
                .order_by("-published_at")[:remaining]
            )
            for p in fallback_posts:
                if p.pk not in collected_post_ids:
                    collected_posts.append(p)
                    collected_post_ids.add(p.pk)
                if len(collected_posts) >= self.limit:
                    break

        return BlogPost.objects.filter(pk__in=[p.pk for p in collected_posts]).order_by("-published_at")
