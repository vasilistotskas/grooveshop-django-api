"""A cache purge must not report success it did not achieve.

Three defects made a failed purge indistinguishable from a clean one,
and a dry run useless:

1. `CustomCache.keys()` logged a warning and returned `[]` on any
   backend failure. `CacheService._purge_surface` wraps that call in an
   `except Exception` written to record exactly such a failure — and the
   except could never fire, because nothing ever reached it. A Redis
   outage read as "no keys matched".

2. `PurgeReport.total_django` sums `django_deleted`, which a dry run
   never increments. So "Dry run: 0 Django + 0 Nuxt keys would be
   removed" was the answer to every dry run ever performed. The real
   count sat in `django_matched` and reached nobody outside the audit
   record's `detail` blob.

3. The admin never surfaced `django_error` at all — only `nuxt_error` —
   so the two above combined into a green "Purged 0 Django keys" while
   the cache was untouched.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.cache import service as cache_service
from core.cache.service import CacheService, PurgeReport, SurfaceResult
from core.caches import CustomCache


class _Boom(Exception):
    pass


@pytest.fixture
def a_surface():
    from core.cache.registry import iter_surfaces

    for surface in iter_surfaces():
        if surface.django_patterns and not surface.nuxt_patterns:
            return surface.code
    return next(iter(iter_surfaces())).code


def test_a_scan_failure_is_raised_not_reported_as_an_empty_match():
    from django.core.cache import cache

    with (
        patch.object(CustomCache, "_make_pattern", side_effect=_Boom("redis")),
        pytest.raises(_Boom),
    ):
        cache.keys("anything:*")


@pytest.mark.django_db
def test_a_backend_failure_lands_on_the_report(a_surface):
    with patch.object(
        cache_service.cache_instance, "keys", side_effect=_Boom("redis is down")
    ):
        report = CacheService.purge([a_surface], include_related=False)

    assert report.failed_surfaces, (
        "a purge that touched nothing must say so — this is the field "
        "the admin and the command both read"
    )
    assert "redis is down" in report.surfaces[0].django_error


@pytest.mark.django_db
def test_a_dry_run_reports_what_it_would_remove(a_surface):
    """The whole point of a dry run is the number it produces."""
    with patch.object(
        cache_service.cache_instance,
        "keys",
        return_value=["k1", "k2", "k3"],
    ):
        report = CacheService.purge(
            [a_surface], dry_run=True, include_related=False
        )

    assert report.total_django == 0, "a dry run deletes nothing"
    assert report.django_headline > 0, (
        "...but it must still report what it matched"
    )
    assert report.django_headline == report.total_django_matched


@pytest.mark.django_db
def test_a_real_run_still_reports_what_it_deleted(a_surface):
    with (
        patch.object(
            cache_service.cache_instance, "keys", return_value=["k1", "k2"]
        ),
        patch.object(
            cache_service.cache_instance, "delete_raw_keys", return_value=2
        ),
    ):
        report = CacheService.purge([a_surface], include_related=False)

    assert report.django_headline == 2
    assert report.django_headline == report.total_django
    assert not report.failed_surfaces


def test_failed_surfaces_covers_every_tier():
    report = PurgeReport(
        surfaces=[
            SurfaceResult(code="a"),
            SurfaceResult(code="b", django_error="redis"),
            SurfaceResult(code="c", nuxt_error="timeout"),
            SurfaceResult(code="d", gateway_error="502"),
        ]
    )

    assert [s.code for s in report.failed_surfaces] == ["b", "c", "d"]


@pytest.mark.django_db
def test_end_to_end_a_dead_backend_does_not_read_as_a_clean_purge(a_surface):
    """The whole chain, with the real `keys()` in it.

    The other backend-failure test patches `keys` itself, so it proves
    the report shape but steps over the swallow. This one breaks the
    scan *inside* `keys()` — where a Redis error actually originates —
    and asserts the failure still reaches the operator. Against the
    previous code this returned a report of zero matches, zero
    deletions and no error at all.
    """
    with patch.object(
        CustomCache, "_make_pattern", side_effect=_Boom("connection refused")
    ):
        report = CacheService.purge([a_surface], include_related=False)

    assert report.failed_surfaces, (
        "a Redis outage rendered as a successful purge of zero keys"
    )
    assert "connection refused" in report.surfaces[0].django_error
    assert report.django_headline == 0
