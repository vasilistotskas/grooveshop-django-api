from django.db.models import Case, IntegerField, Value, When

from blog.models.post import BlogPost
from blog.strategies.related_posts_strategy import RelatedPostsStrategy


class WeightedRelatedPostsStrategy(RelatedPostsStrategy):
    def __init__(
        self,
        strategies_with_weights: list[tuple[RelatedPostsStrategy, float]],
        limit: int = 8,
    ):
        self.strategies_with_weights = strategies_with_weights
        self.limit = limit

    def get_related_posts(self, post: BlogPost):
        collected_posts = []
        collected_post_ids = set()
        remaining_limit = self.limit

        for strategy, weight in self.strategies_with_weights:
            num_posts = int(weight * self.limit)
            num_posts = min(num_posts, remaining_limit)
            if num_posts <= 0:
                continue

            posts = strategy.get_related_posts(post).exclude(
                pk__in=collected_post_ids
            )[:num_posts]
            collected_posts.extend(posts)
            collected_post_ids.update(post.pk for post in posts)
            remaining_limit -= len(posts)
            if remaining_limit <= 0:
                break

        if remaining_limit > 0:
            for strategy, _weight in self.strategies_with_weights:
                posts = strategy.get_related_posts(post).exclude(
                    pk__in=collected_post_ids
                )[:remaining_limit]
                for p in posts:
                    if p.pk not in collected_post_ids:
                        collected_posts.append(p)
                        collected_post_ids.add(p.pk)
                        remaining_limit -= 1
                        if remaining_limit <= 0:
                            break
                if remaining_limit <= 0:
                    break

        # ``for_list``, because the serializer reads each post's author,
        # translations, tags and the three counts: a bare queryset made
        # that six queries per post. In the order the strategies found
        # them: the first strategy is the closest match (the lexical one
        # ranks by likeness), and re-sorting by date threw that away.
        ranked = [p.pk for p in collected_posts]
        if not ranked:
            return BlogPost.objects.for_list().none()
        return (
            BlogPost.objects.for_list()
            .filter(pk__in=ranked)
            .order_by(
                Case(
                    *(
                        When(pk=pk, then=Value(i))
                        for i, pk in enumerate(ranked)
                    ),
                    output_field=IntegerField(),
                )
            )
        )
