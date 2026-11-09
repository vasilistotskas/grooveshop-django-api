from blog.models.post import BlogPost


class RelatedPostsStrategy:
    """One way to find a post's related posts: a member of the chain
    ``WeightedRelatedPostsStrategy`` asks, in its settings' order
    (``RELATED_POSTS_STRATEGIES``)."""

    def related_ids(self, post: BlogPost, limit: int) -> list[int]:
        """Up to ``limit`` ids of published posts related to ``post``,
        the closest first, never ``post`` itself."""
        raise NotImplementedError("Subclasses must implement this method.")
