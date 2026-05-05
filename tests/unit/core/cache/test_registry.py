from __future__ import annotations

import pytest

from core.cache.registry import (
    CacheSurface,
    expand_with_related,
    get_surface,
    iter_surfaces,
    register_surface,
    reset,
)


@pytest.fixture
def empty_registry():
    reset()
    yield
    reset()
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
