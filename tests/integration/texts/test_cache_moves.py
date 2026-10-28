"""A save moves everything that caches the store's wording.

* the store's cache generation (the overrides read is keyed by it);
* the ``store_texts`` surface: the storefront's copy and rendered pages,
  with ``tenantConfig`` so it re-resolves the store, and the edge purge
  the route pattern queues.

Real commits (``transaction=True``): the purge is queued on commit, which
the suite's autouse fixture otherwise runs at once.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.db import transaction

from core.cache.invalidation import (
    _pending_codes,
    connect_surface_invalidation,
    disconnect_surface_invalidation,
    run_queued_purge,
)
from core.cache.registry import get_surface
from tenant.models import Tenant
from tests.utils.texts import FOOTER
from texts import services
from texts.factories import TextOverrideFactory

real_commits = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _connected():
    _pending_codes().clear()
    connect_surface_invalidation(force=True)
    yield
    disconnect_surface_invalidation()
    _pending_codes().clear()


@pytest.fixture
def queued():
    """The purge task, as queued. Eager Celery drops the schema header a
    real worker receives, so the task body is run by hand under the bound
    store (``run_queued_purge``)."""
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


def test_the_surface_purges_the_storefront_with_the_tenant_config():
    surface = get_surface("store_texts")

    assert surface.invalidates_tenant_config is True
    assert surface.invalidated_by == ("texts.TextOverride",)
    assert "cache:nitro:handlers:textOverrides*" in surface.nuxt_patterns
    # A route-family pattern is what queues the Cloudflare edge purge.
    assert "cache:nitro:routes:_:*" in surface.nuxt_patterns


@pytest.mark.django_db
def test_purging_the_surface_asks_the_storefront_with_tenant_config():
    from core.cache.service import CacheService

    with (
        mock.patch("core.cache.service.nuxt_client") as nuxt,
        mock.patch("core.cache.service.cache_instance"),
    ):
        nuxt.request_purge.return_value = mock.Mock(
            matched=0, deleted=0, blocked=0, error=None
        )
        CacheService.purge(["store_texts"], include_related=False)

    patterns = nuxt.request_purge.call_args.args[0]
    assert "cache:nitro:handlers:textOverrides*" in patterns
    assert nuxt.request_purge.call_args.kwargs["tenant_config"] is True


@real_commits
class TestSaving:
    def test_a_save_purges_the_surface_after_commit(
        self, stub, tenant, admin_user, purge, queued
    ):
        with transaction.atomic():
            services.save_texts(
                admin_user,
                namespace=FOOTER,
                key="tagline",
                values={"en": "Welcome"},
            )
            assert queued.call_count == 0, "must wait for commit"

        assert queued.call_count == 1
        assert run_queued_purge() == ["store_texts"]
        assert list(purge.call_args.args[0]) == ["store_texts"]

    def test_a_save_moves_the_cache_generation(
        self, stub, tenant, admin_user, purge
    ):
        before = _generation(tenant)

        services.save_texts(
            admin_user,
            namespace=FOOTER,
            key="tagline",
            values={"en": "Welcome"},
        )

        assert _generation(tenant) == before + 1

    def test_a_reset_purges_and_moves_the_generation_too(
        self, stub, tenant, admin_user, purge
    ):
        TextOverrideFactory(namespace=FOOTER, key="tagline", language="en")
        run_queued_purge()
        purge.reset_mock()
        before = _generation(tenant)

        services.reset_text(admin_user, namespace=FOOTER, key="tagline")

        assert run_queued_purge() == ["store_texts"]
        assert _generation(tenant) == before + 1

    def test_a_refused_save_moves_nothing(
        self, stub, tenant, admin_user, purge
    ):
        stub.verdict = lambda *a: ("syntax_error", "bad")
        before = _generation(tenant)

        services.save_texts(
            admin_user,
            namespace=FOOTER,
            key="tagline",
            values={"en": "{"},
        )

        assert run_queued_purge() == []
        assert purge.call_count == 0
        assert _generation(tenant) == before
