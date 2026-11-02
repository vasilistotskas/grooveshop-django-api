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


class TestImportAndReset:
    """The import and the reset write releases and delete them outside the
    publish path, and must move the same things a publish moves: release
    numbers restart at 1, so a fresh release 1 would otherwise be served
    from the storefront's caches of the old one."""

    @pytest.fixture
    def imported(self, tenant, catalogue, features_on, purge):
        from builder import importing
        from tests.integration.builder.test_legacy_import import (
            fake_convert,
            make_layout,
        )

        make_layout(sections=2)
        with mock.patch(
            "builder.importing.convert_legacy", side_effect=fake_convert
        ):
            report = importing.import_store()
        assert report.release == 1
        return report

    def test_an_import_purges_the_surface_and_moves_the_generation(
        self, tenant, catalogue, features_on, purge
    ):
        from builder import importing
        from tests.integration.builder.test_legacy_import import (
            fake_convert,
            make_layout,
        )

        make_layout(sections=2)
        # The legacy rows have their own purge: only the import's counts.
        run_queued_purge()
        purge.reset_mock()
        before = _generation(tenant)

        with mock.patch(
            "builder.importing.convert_legacy", side_effect=fake_convert
        ):
            importing.import_store()

        assert run_queued_purge() == ["builder"]
        assert purge.call_count == 1
        assert _generation(tenant) == before + 1

    def test_a_dry_run_import_moves_nothing(
        self, tenant, catalogue, features_on, purge
    ):
        from builder import importing
        from tests.integration.builder.test_legacy_import import (
            fake_convert,
            make_layout,
        )

        make_layout(sections=2)
        # The legacy rows have their own purge: only the import's counts.
        run_queued_purge()
        purge.reset_mock()
        before = _generation(tenant)

        with mock.patch(
            "builder.importing.convert_legacy", side_effect=fake_convert
        ):
            importing.import_store(dry_run=True)

        assert run_queued_purge() == []
        assert _generation(tenant) == before

    def test_a_reset_purges_the_surface_and_moves_the_generation(
        self, imported, tenant, purge
    ):
        from builder import resetting

        run_queued_purge()
        purge.reset_mock()
        before = _generation(tenant)

        report = resetting.reset_store()

        assert report.applied
        assert run_queued_purge() == ["builder"]
        assert purge.call_count == 1
        assert _generation(tenant) > before

    def test_a_reset_dry_run_moves_nothing(self, imported, tenant, purge):
        from builder import resetting

        run_queued_purge()
        purge.reset_mock()
        before = _generation(tenant)

        resetting.reset_store(dry_run=True)

        assert run_queued_purge() == []
        assert _generation(tenant) == before
