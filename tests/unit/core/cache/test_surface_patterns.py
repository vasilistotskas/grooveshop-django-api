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

from functools import lru_cache

from core.cache.registry import get_surface
from core.cache.surfaces import (
    _escaped_pathname,
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


class TestSiteRootPathname:
    """Nitro stores "/" under the literal segment ``index``.

    Verified in the live staging keyspace:
    ``cache:nitro:routes:_:index.il7asoJjJE:host.<hash>:xdeviceclass.<hash>.json``

    Before this was handled, ``_nuxt_routes("/")`` stripped every
    non-word character to an empty string and produced
    ``cache:nitro:routes:_:**`` — matching EVERY cached page render, so
    a caller wanting to drop just the homepage dropped the whole site's
    SSR cache. Measured on staging: the broad form matched 9 route keys,
    the ``index`` form matches 1.
    """

    def test_root_maps_to_the_index_segment(self):
        assert _escaped_pathname("/") == "index"
        assert _escaped_pathname("") == "index"

    def test_root_pattern_is_not_a_catch_all(self):
        (pattern,) = _nuxt_routes("/")

        assert pattern == "cache:nitro:routes:_:*index*"
        assert pattern != "cache:nitro:routes:_:**"

    def test_ordinary_paths_are_unchanged(self):
        assert _escaped_pathname("/about") == "about"
        assert _escaped_pathname("/products/category") == "productscategory"

    def test_pathname_is_truncated_to_sixteen_chars(self):
        assert _escaped_pathname("/a-very-long-path-that-keeps-going") == (
            "averylongpaththa"
        )


class TestPageConfigSurface:
    """The page builder had no surface at all, so a layout edit sat
    behind Nitro's SSR cache for the rest of its TTL with no way to
    flush it."""

    def test_purges_both_the_json_and_the_rendered_html(self):
        patterns = get_surface("page_config").nuxt_patterns

        # The JSON the pages are built from...
        assert "cache:nitro:handlers:pageConfig*" in patterns
        # ...and the HTML already built from it. Purging only the first
        # leaves the storefront serving the old page.
        assert "cache:nitro:routes:_:*index*" in patterns

    def test_covers_the_builder_driven_page_types_only(self):
        """Not a blanket route purge: usePageConfig is called with a
        fixed set of page types, so a layout edit should not evict the
        catalogue's and blog's renders too."""
        patterns = get_surface("page_config").nuxt_patterns

        assert "cache:nitro:routes:_:*" not in patterns
        for path in ("index", "about", "contact", "feedback"):
            assert f"cache:nitro:routes:_:*{path}*" in patterns

    def test_declares_no_django_patterns(self):
        """Nothing in page_config is ``@cache_methods``-decorated — not
        the plain @api_view endpoints and not ContentPageViewSet either.
        A pattern here would match zero keys and report success, which
        is the failure mode this whole module guards against.
        """
        assert get_surface("page_config").django_patterns == ()


class TestPromotionsSurface:
    def test_purges_the_offers_handler_and_page(self):
        patterns = get_surface("promotions").nuxt_patterns

        assert "cache:nitro:handlers:PublicPromotionList*" in patterns
        assert "cache:nitro:routes:_:*offers*" in patterns


class TestNoDeadDjangoPatterns:
    """Registry-wide invariant: a viewset-shaped pattern must name a
    class that actually carries ``@cache_methods``.

    ``cache_methods`` builds each key prefix as ``{ClassName}_{method}``,
    so ``*SomeViewSet_*`` only matches if SomeViewSet is decorated. A
    pattern naming an undecorated class matches ZERO keys and the purge
    reports success while the storefront keeps serving stale content.

    Two live instances of exactly that were found and removed on
    2026-09-01: ``*SettingsViewSet_*`` on the settings surface (no such
    class exists — the settings API is two plain @api_view functions)
    and ``*ContentPageViewSet_*`` on a newly added page_config surface
    (the class exists but carries no decorator).

    This test is the general guard, so the next one cannot be added by
    hand without being caught.
    """

    @staticmethod
    @lru_cache(maxsize=1)
    def _decorated_class_names() -> frozenset[str]:
        """Every class carrying ``@cache_methods``, read from source.

        Cached: the scan walks the whole repo, and running it once per
        test doubled this module's runtime.

        AST rather than import-and-introspect: the decorator wraps
        methods in place and leaves no marker on the class, so there is
        nothing to detect at runtime.
        """
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[4]
        names: set[str] = set()
        for path in root.rglob("*.py"):
            parts = set(path.parts)
            if ".venv" in parts or "node_modules" in parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError, UnicodeDecodeError:
                continue
            if "@cache_methods" not in text:
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for dec in node.decorator_list:
                    func = dec.func if isinstance(dec, ast.Call) else dec
                    if (
                        isinstance(func, ast.Name)
                        and func.id == "cache_methods"
                    ):
                        names.add(node.name)
        return frozenset(names)

    def test_every_viewset_pattern_names_a_decorated_class(self):
        import re

        from core.cache.registry import iter_surfaces

        decorated = self._decorated_class_names()
        # Sanity: the scan itself must work, or this test passes
        # vacuously for the worst possible reason.
        assert "BlogPostViewSet" in decorated, decorated
        assert "ProductCategoryViewSet" in decorated

        dead = []
        for surface in iter_surfaces():
            for pattern in surface.django_patterns:
                match = re.fullmatch(r"\*([A-Za-z0-9]+ViewSet)_\*", pattern)
                if match and match.group(1) not in decorated:
                    dead.append((surface.code, pattern))

        assert not dead, (
            f"These patterns match no cache key because the class is not "
            f"@cache_methods-decorated: {dead}"
        )

    def test_every_decorated_class_is_purgeable(self):
        """The reverse gap: a cached viewset no surface can invalidate
        keeps serving stale data for the full DEFAULT_CACHE_TTL with no
        operator recourse.
        """
        import re

        from core.cache.registry import iter_surfaces

        covered = set()
        for surface in iter_surfaces():
            for pattern in surface.django_patterns:
                match = re.fullmatch(r"\*([A-Za-z0-9]+ViewSet)_\*", pattern)
                if match:
                    covered.add(match.group(1))

        uncovered = sorted(self._decorated_class_names() - covered)

        assert not uncovered, (
            f"@cache_methods-decorated but no surface purges them: {uncovered}"
        )
