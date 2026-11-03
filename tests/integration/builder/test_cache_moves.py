"""A release moves everything that caches the store's pages; a draft
save moves nothing.

Real commits (``transaction=True``): the purge is queued on commit, which
the suite's autouse fixture otherwise runs at once.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.db import transaction

from builder import services
from builder.errors import DocumentInvalid
from core.cache.invalidation import (
    _pending_codes,
    connect_surface_invalidation,
    disconnect_surface_invalidation,
    run_queued_purge,
)
from core.cache.registry import get_surface
from tenant.models import Tenant
from tests.utils.builder import document, hero

pytestmark = pytest.mark.django_db(transaction=True)

HOME = "template:home"


@pytest.fixture(autouse=True)
def _connected():
    _pending_codes().clear()
    connect_surface_invalidation(force=True)
    yield
    disconnect_surface_invalidation()
    _pending_codes().clear()


@pytest.fixture
def queued():
    with mock.patch(
        "core.tasks.invalidate_cache_surfaces_task.apply_async"
    ) as apply_async:
        yield apply_async


@pytest.fixture
def purge(queued):
    with mock.patch("core.cache.service.CacheService.purge") as purge_mock:
        purge_mock.return_value = mock.Mock(
            failed_surfaces=[], django_headline=0, nuxt_headline=1
        )
        yield purge_mock


def _generation(tenant) -> int:
    return Tenant.objects.get(pk=tenant.pk).cache_generation


def _save(admin, revision=0, title="Welcome"):
    return services.save_draft(
        admin,
        key=HOME,
        draft=document(("a", hero(title))),
        i18n={},
        expected_revision=revision,
    )


def test_the_surface_purges_the_storefront_with_the_tenant_config():
    surface = get_surface("builder")

    assert surface.invalidates_tenant_config is True
    assert surface.invalidated_by == ("builder.BuilderRelease",)
    assert "cache:nitro:handlers:builderPage*" in surface.nuxt_patterns
    assert "cache:nitro:functions:builderPageDocument*" in surface.nuxt_patterns
    assert "cache:nitro:handlers:builderChrome*" in surface.nuxt_patterns
    # A route-family pattern is what queues the Cloudflare edge purge.
    assert "cache:nitro:routes:_:*" in surface.nuxt_patterns


def test_a_publish_purges_the_surface_after_commit_and_moves_the_generation(
    tenant, admin, catalogue, features_on, purge, queued
):
    _save(admin)
    run_queued_purge()
    purge.reset_mock()
    queued.reset_mock()
    before = _generation(tenant)

    with transaction.atomic():
        services.publish(admin, keys=[HOME])
        assert queued.call_count == 0, "must wait for commit"

    assert queued.call_count == 1
    assert run_queued_purge() == ["builder"]
    assert _generation(tenant) == before + 1


def test_a_rollback_moves_both_too(
    tenant, admin, catalogue, features_on, purge
):
    _save(admin)
    first = services.publish(admin, keys=[HOME]).release
    _save(admin, 1, "Other")
    services.publish(admin, keys=[HOME])
    run_queued_purge()
    purge.reset_mock()
    before = _generation(tenant)

    services.rollback(admin, release_id=first.pk)

    assert run_queued_purge() == ["builder"]
    assert _generation(tenant) == before + 1


def test_a_draft_save_and_a_checkpoint_purge_nothing(
    tenant, admin, catalogue, features_on, purge
):
    before = _generation(tenant)

    _save(admin)
    services.create_checkpoint(admin, key=HOME)

    assert run_queued_purge() == []
    assert purge.call_count == 0
    assert _generation(tenant) == before


def test_a_refused_publish_moves_nothing(
    tenant, admin, catalogue, features_on, purge
):
    services.save_draft(
        admin,
        key=HOME,
        draft=document(("a", hero(productId=987654))),
        i18n={},
        expected_revision=0,
    )
    before = _generation(tenant)

    with pytest.raises(DocumentInvalid):
        services.publish(admin, keys=[HOME])

    assert run_queued_purge() == []
    assert _generation(tenant) == before


def test_the_data_loaders_are_purged_with_the_records_they_read():
    """The storefront's ``builderData*`` functions (60 s) read products,
    categories, posts and offers; each surface purges the ones it feeds."""
    from core.cache.registry import get_surface

    expected = {
        "products": {"builderDataProduct", "builderDataSlug"},
        "categories": {"builderDataCategory", "builderDataSlug"},
        "blog": {
            "builderDataBlogPost",
            "builderDataBlogCategory",
            "builderDataSlug",
        },
        "promotions": {"builderDataPromotion", "builderDataProduct"},
        "builder": {"builderPageDocument", "builderDataTargets"},
        "page_config": {"builderDataTargets"},
    }
    for code, names in expected.items():
        patterns = set(get_surface(code).nuxt_patterns)
        for name in names:
            assert f"cache:nitro:functions:{name}*" in patterns, (code, name)
