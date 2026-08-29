"""The Nuxt purge patterns encode Nitro's Redis key layout.

Nitro stores each cache family under a different prefix, and a pattern
aimed at the wrong one silently matches nothing — the purge reports
success while the storefront keeps serving stale content. That is exactly
what the sitemap/RSS surface did before 2026-08-29, so these shapes are
pinned here against the layout observed in the live keyspace:

    cache:nitro:handlers:BlogCategoryDetail:websidegrblogcategory5_<hash>.json
    cache:nitro:functions:sitemap:blog-posts:webside.gr:...json
    cache:nitro:routes:_:index.<hash>:host.<hash>:xdeviceclass.<hash>.json
"""

from __future__ import annotations

from core.cache.registry import get_surface
from core.cache.surfaces import (
    _nuxt,
    _nuxt_functions,
    _nuxt_matching,
    _nuxt_routes,
)


class TestPatternBuilders:
    def test_handler_patterns_target_the_handlers_prefix(self):
        assert _nuxt("BlogPostViewSet") == (
            "cache:nitro:handlers:BlogPostViewSet*",
        )

    def test_function_patterns_target_the_functions_prefix(self):
        assert _nuxt_functions("sitemap", "RssFeed") == (
            "cache:nitro:functions:sitemap*",
            "cache:nitro:functions:RssFeed*",
        )

    def test_fragment_patterns_lead_with_a_star(self):
        """Measured on production: ``Blog*`` -> 0 keys, ``*Blog*`` -> 270.

        ``getKeys()`` resolves its argument as a key PATH, so a bare
        prefix only matches whole ``:`` segments and a handler name is
        one whole segment (``BlogCategoryDetail``). Only a LEADING star
        reaches the purge endpoint's regex post-filter.
        """
        assert _nuxt_matching("Blog") == ("cache:nitro:handlers:*Blog*",)

    def test_route_patterns_flatten_the_path_like_nitro_does(self):
        """Nitro strips non-word chars and truncates the path to 16."""
        assert _nuxt_routes("/blog") == ("cache:nitro:routes:_:*blog*",)
        assert _nuxt_routes("/products") == ("cache:nitro:routes:_:*products*",)

    def test_route_pattern_matches_every_page_in_the_family(self):
        """One pattern clears a whole family; the endpoint globs it."""
        import re

        (pattern,) = _nuxt_routes("/blog")
        # Mirror the purge endpoint's mid-glob handling.
        regex = re.compile(
            "^"
            + ".*".join(re.escape(part) for part in pattern.split("*"))
            + "$"
        )

        # Keys Nitro would write for the SWR-cached blog pages.
        for key in (
            "cache:nitro:routes:_:blog.abc123:host.def.json",
            "cache:nitro:routes:_:blogcategories.a:host.b.json",
            "cache:nitro:routes:_:blogcategory5PC.a:host.b.json",
            "cache:nitro:routes:_:blogpost42mnhmhr.a:host.b.json",
        ):
            assert regex.match(key), key

        assert not regex.match("cache:nitro:routes:_:index.a:host.b.json")


class TestSurfacesPurgeRenderedPages:
    """A merchant edit must drop the HTML, not just the JSON behind it."""

    def test_blog_surface_purges_blog_pages(self):
        patterns = get_surface("blog").nuxt_patterns
        assert "cache:nitro:routes:_:*blog*" in patterns
        # Regression: the bare-prefix form matched zero keys.
        assert "cache:nitro:handlers:*Blog*" in patterns
        assert "cache:nitro:handlers:Blog*" not in patterns

    def test_product_surfaces_purge_catalogue_pages(self):
        for code in ("products", "categories"):
            assert (
                "cache:nitro:routes:_:*products*"
                in get_surface(code).nuxt_patterns
            ), code

    def test_sitemap_surface_targets_cached_functions(self):
        patterns = get_surface("sitemap_seo").nuxt_patterns
        assert "cache:nitro:functions:sitemap*" in patterns
        assert "cache:nitro:functions:RssFeed*" in patterns
        # Regression: these used to be `cache:nitro:routes:__sitemap__*`
        # and `:rss*`, which match no key Nitro ever writes.
        assert not any(p.startswith("cache:nitro:routes:__") for p in patterns)
