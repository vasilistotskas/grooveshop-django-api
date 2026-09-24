"""The response cache is scoped to the running release.

A deploy that changes what an endpoint returns must never serve a body
an earlier release cached, and the admin purge patterns must still reach
every entry whatever release wrote it.
"""

from __future__ import annotations

import tomllib
from fnmatch import fnmatchcase

from django.conf import settings
from django.test import override_settings

from core.cache.registry import iter_surfaces
from core.utils.views import response_cache_key_prefix


def test_release_version_is_the_shipped_pyproject_version():
    with (settings.BASE_DIR / "pyproject.toml").open("rb") as pyproject:
        expected = tomllib.load(pyproject)["project"]["version"]

    assert settings.RELEASE_VERSION == expected


def test_the_prefix_leads_with_the_release():
    with override_settings(RELEASE_VERSION="9.9.9"):
        assert (
            response_cache_key_prefix("BlogPostViewSet", "list")
            == "9.9.9.BlogPostViewSet_list"
        )


def test_two_releases_never_share_a_prefix():
    with override_settings(RELEASE_VERSION="3.80.0"):
        before = response_cache_key_prefix("ProductViewSet", "retrieve")
    with override_settings(RELEASE_VERSION="3.81.0"):
        after = response_cache_key_prefix("ProductViewSet", "retrieve")

    assert before != after


def test_every_purge_pattern_still_matches_a_release_scoped_key():
    """``cache_page`` keys read ``views.decorators.cache.cache_page.
    <prefix>.<method>.<hash>.<lang>`` inside the tenant and backend
    namespace, and the surfaces' ``*<Class>ViewSet_*`` globs must match
    them with the release segment in front."""
    patterns = [
        pattern
        for surface in iter_surfaces()
        for pattern in surface.django_patterns
        if pattern.endswith("ViewSet_*")
    ]
    assert patterns

    for pattern in patterns:
        class_name = pattern.strip("*").removesuffix("_")
        key = (
            "demo:default:1:views.decorators.cache.cache_page."
            f"{response_cache_key_prefix(class_name, 'list')}"
            ".GET.0123456789abcdef.en"
        )
        assert fnmatchcase(key, pattern), (pattern, key)
