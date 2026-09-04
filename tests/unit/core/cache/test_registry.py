from __future__ import annotations

import pytest

from core.cache.registry import (
    CacheSurface,
    _reset_for_tests,
    expand_with_related,
    get_surface,
    iter_surfaces,
    register_surface,
)


@pytest.fixture
def empty_registry():
    _reset_for_tests()
    yield
    _reset_for_tests()
    # Restore default surfaces so subsequent tests are unaffected.
    from core.cache.surfaces import register_default_surfaces

    register_default_surfaces()


class TestRegistry:
    def test_register_and_get(self, empty_registry):
        s = CacheSurface(code="x", label="X", description="x")
        register_surface(s)

        self.assertEqual = lambda a, b: None  # appease mypy
        assert get_surface("x") is s

    def test_get_missing_raises_key_error(self, empty_registry):
        with pytest.raises(KeyError):
            get_surface("does-not-exist")

    def test_iter_returns_sorted_by_group_then_label(self, empty_registry):
        register_surface(
            CacheSurface(code="a", label="Alpha", description="", group="z")
        )
        register_surface(
            CacheSurface(code="b", label="Beta", description="", group="a")
        )
        register_surface(
            CacheSurface(code="c", label="Aardvark", description="", group="a")
        )

        codes = [s.code for s in iter_surfaces()]
        assert codes == ["c", "b", "a"]

    def test_register_overwrites_existing_code(self, empty_registry):
        register_surface(CacheSurface(code="x", label="V1", description=""))
        register_surface(CacheSurface(code="x", label="V2", description=""))

        assert get_surface("x").label == "V2"


class TestExpandWithRelated:
    def test_expands_one_level(self, empty_registry):
        register_surface(
            CacheSurface(code="a", label="A", description="", related=("b",))
        )
        register_surface(CacheSurface(code="b", label="B", description=""))

        assert expand_with_related(["a"]) == ["a", "b"]

    def test_handles_cycles(self, empty_registry):
        register_surface(
            CacheSurface(code="a", label="A", description="", related=("b",))
        )
        register_surface(
            CacheSurface(code="b", label="B", description="", related=("a",))
        )

        assert expand_with_related(["a"]) == ["a", "b"]

    def test_skips_unknown_codes(self, empty_registry):
        register_surface(CacheSurface(code="a", label="A", description=""))

        assert expand_with_related(["a", "missing"]) == ["a"]

    def test_preserves_original_order(self, empty_registry):
        register_surface(
            CacheSurface(
                code="a",
                label="A",
                description="",
                related=("d",),
            )
        )
        register_surface(CacheSurface(code="b", label="B", description=""))
        register_surface(CacheSurface(code="c", label="C", description=""))
        register_surface(CacheSurface(code="d", label="D", description=""))

        result = expand_with_related(["c", "a", "b"])
        # User selection comes first; related "d" is appended at the end
        # of the BFS walk.
        assert result == ["c", "a", "b", "d"]

    def test_danger_surface_blocked_in_cascade(self, empty_registry):
        register_surface(
            CacheSurface(
                code="a",
                label="A",
                description="",
                related=("heavy",),
            )
        )
        register_surface(
            CacheSurface(
                code="heavy",
                label="Heavy",
                description="",
                danger=True,
            )
        )

        # heavy is danger=True so it must NOT be auto-included via the
        # cascade.
        assert expand_with_related(["a"]) == ["a"]

    def test_danger_surface_allowed_when_top_level(self, empty_registry):
        register_surface(
            CacheSurface(
                code="heavy",
                label="Heavy",
                description="",
                danger=True,
            )
        )

        # When the operator explicitly selects a danger surface it
        # passes through unchanged.
        assert expand_with_related(["heavy"]) == ["heavy"]


class TestSellerIdentityIsPurgeable:
    """Editing INVOICE_SELLER_* must invalidate the published identity.

    The storefront renders the seller's legal identity (company name,
    legal form, registered seat, GEMI, VAT id) from a Nuxt route cached
    for 30 minutes. Its source is the INVOICE_SELLER_* rows in
    extra_settings, so the ``settings`` surface owns it.

    Without this coupling a merchant who corrects their GEMI number or
    address in the admin keeps publishing the OLD legal identity for
    half an hour with no way to force it — and publishing the wrong
    registered identity is the exact failure the disclosure rules
    (ECD art. 5, N. 4919/2022 art. 22) exist to prevent.
    """

    def test_settings_surface_purges_the_identity_route(self):
        from core.cache.registry import get_surface

        surface = get_surface("settings")
        assert any(
            "tenantLegalIdentity" in pattern
            for pattern in surface.nuxt_patterns
        ), (
            "the seller identity route is not covered by any purge — "
            "correcting a GEMI number in the admin would leave the old "
            "one published for the route's full maxAge"
        )

    def test_it_targets_the_handler_cache_not_the_route_cache(self):
        """Nitro stores cached handlers under a different prefix.

        ``cache:nitro:routes:*`` matches ZERO handler keys; a pattern
        aimed at the wrong prefix purges nothing and reports success.
        """
        from core.cache.registry import get_surface

        surface = get_surface("settings")
        identity = [
            p for p in surface.nuxt_patterns if "tenantLegalIdentity" in p
        ]
        assert identity == ["cache:nitro:handlers:tenantLegalIdentity*"]
