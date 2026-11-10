from django.db.models import Case, IntegerField, Value, When

from blog.models.post import BlogPost
from blog.strategies.related_posts_strategy import RelatedPostsStrategy


class WeightedRelatedPostsStrategy:
    """A post's related posts, from a chain of strategies.

    Each strategy first gets its weight's share of ``limit`` (in chain
    order, the first being the closest match), then the chain fills what
    is left in the same order. Each strategy is asked once, for ids, and
    the lists are merged here, so the number of queries does not depend
    on what the strategies return.
    """

    def __init__(
        self,
        strategies_with_weights: list[tuple[RelatedPostsStrategy, float]],
        limit: int = 8,
    ):
        self.strategies_with_weights = strategies_with_weights
        self.limit = limit

    def get_related_posts(self, post: BlogPost):
        # Enough ids from each that removing what an earlier strategy took
        # still leaves ``limit`` to choose from.
        depth = self.limit * len(self.strategies_with_weights)
        found = [
            strategy.related_ids(post, depth)
            for strategy, _weight in self.strategies_with_weights
        ]

        ranked: list[int] = []

        def take(ids: list[int], count: int) -> None:
            for pk in ids:
                if count <= 0:
                    return
                if pk not in ranked:
                    ranked.append(pk)
                    count -= 1

        for (_strategy, weight), ids in zip(
            self.strategies_with_weights, found, strict=True
        ):
            take(ids, min(int(weight * self.limit), self.limit - len(ranked)))
        for ids in found:
            take(ids, self.limit - len(ranked))

        # ``for_list``, because the serializer reads each post's author,
        # translations, tags and the three counts: a bare queryset made
        # that six queries per post. In the order the strategies found
        # them: the first strategy is the closest match (the shared-words
        # one ranks by likeness), and re-sorting by date threw that away.
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
