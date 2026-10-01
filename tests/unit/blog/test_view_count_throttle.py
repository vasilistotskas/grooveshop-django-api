"""Every view-count increment is throttled per visitor.

The product and blog-post counters are both public POSTs that anyone can
replay to inflate a ranking. The product action had ``ViewCountThrottle``
and the blog one did not, so a script could push a post up "most viewed"
without limit. They share the ``view_count`` scope: one budget per
visitor across both counters.
"""

from __future__ import annotations

import pytest

from blog.views.post import BlogPostViewSet
from core.api.throttling import ViewCountThrottle
from product.views.product import ProductViewSet


@pytest.mark.parametrize("viewset", [BlogPostViewSet, ProductViewSet])
def test_view_count_is_throttled_per_visitor(viewset):
    assert viewset.update_view_count.kwargs["throttle_classes"] == [
        ViewCountThrottle
    ]
