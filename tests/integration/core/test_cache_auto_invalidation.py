"""An operator edit must not stay invisible behind the cache.

``CacheService.purge`` had two callers — the admin Cache Management
page and the ``clear_cache`` command — so nothing invalidated on a
write. A PayWay description corrected in Django admin kept serving the
previous copy from Django's ``cache_page`` entry (``DEFAULT_CACHE_TTL``,
7200s) and from Nitro's own entry layered on top. Observed in production
on 2026-09-10: the Greek storefront showed an English bank-transfer
placeholder at checkout hours after the database was right.

The subtle half is ``PayWayTranslation``. parler keeps each language in
its own row, so editing the Greek description never touches ``PayWay``
— a receiver bound only to the master model would look correct and fire
on none of the edits that actually go stale.

The classes asserting deferral, coalescing or rollback are
``transaction=True``, deliberately. The suite's autouse
``_run_transaction_on_commit_immediately`` fixture rewrites
``transaction.on_commit`` into a direct call, and that fixture skips
itself for transactional tests. Without real commits those assertions
would be measuring the fixture, not the code. Failure isolation and
cross-transaction batching hold either way, so they skip the flush.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.db import transaction

from core.cache.invalidation import (
    _pending_codes,
    connect_surface_invalidation,
    disconnect_surface_invalidation,
)
from pay_way.models import PayWay

real_commits = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _connected():
    """``DISABLE_CACHE`` is True for the suite, so the receivers are not
    wired at startup — nothing is cached, so nothing needs invalidating,
    and connecting would make every factory-built row in every other
    test reach for a Redis client that is not there. This module is the
    exception: it tests the wiring, so it opts in and cleans up."""
    _pending_codes().clear()
    connect_surface_invalidation(force=True)
    yield
    disconnect_surface_invalidation()
    _pending_codes().clear()


@pytest.fixture
def purge():
    """Patch the service where ``_flush`` imports it, so no real Redis
    SCAN or Nuxt POST runs."""
    with mock.patch("core.cache.service.CacheService.purge") as purge_mock:
        purge_mock.return_value = mock.Mock(
            failed_surfaces=[], django_headline=3, nuxt_headline=1
        )
        yield purge_mock


def _codes(purge_mock) -> list[str]:
    assert purge_mock.call_count == 1, (
        f"expected exactly one purge, got {purge_mock.call_count}"
    )
    return list(purge_mock.call_args.args[0])


@real_commits
class TestWritesInvalidate:
    def test_saving_a_pay_way_purges_its_surface_on_commit(self, purge):
        with transaction.atomic():
            pay_way = PayWay.objects.create(provider_code="test_provider")
            assert purge.call_count == 0, "must wait for commit"

        assert _codes(purge) == ["pay_way"]
        assert pay_way.pk is not None

    def test_editing_only_a_translation_purges_too(self, purge):
        """A translation row written on its own, with the master
        untouched.

        Note that ``pay_way.save()`` would NOT prove this: parler's
        ``TranslatableModel.save()`` writes the master first and then
        the translations, so the master's ``post_save`` fires either
        way and the assertion would hold even with
        ``PayWayTranslation`` unregistered. Saving the translation row
        directly is the shape that isolates it — and it is a real path,
        because the admin's parler save fix upserts translation rows.
        """
        pay_way = PayWay.objects.create(provider_code="test_provider")
        pay_way.set_current_language("el")
        pay_way.description = "Αρχική"
        pay_way.save()
        purge.reset_mock()
        _pending_codes().clear()

        translation = pay_way.translations.get(language_code="el")

        with transaction.atomic():
            translation.description = "Νέα περιγραφή"
            translation.save(update_fields=["description"])

        assert _codes(purge) == ["pay_way"]

    def test_a_queryset_update_on_translations_is_a_known_blind_spot(self):
        """``QuerySet.update()`` emits no ``post_save`` — Django does not
        instantiate the rows — so a bulk translation rewrite does NOT
        auto-invalidate. Documented here rather than silently assumed:
        code taking that path must purge explicitly.
        """
        # parler builds the translation model at runtime, so it is not
        # importable from ``pay_way.models`` — the app registry is the
        # only handle, which is also why the surface names it as a
        # ``"app_label.ModelName"`` string.
        from django.apps import apps

        translation_model = apps.get_model("pay_way", "PayWayTranslation")

        pay_way = PayWay.objects.create(provider_code="test_provider")
        pay_way.set_current_language("el")
        pay_way.description = "Αρχική"
        pay_way.save()
        _pending_codes().clear()

        with mock.patch("core.cache.service.CacheService.purge") as purge_mock:
            with transaction.atomic():
                translation_model.objects.filter(master=pay_way).update(
                    description="Παρακάμπτει τα σήματα"
                )

        assert purge_mock.call_count == 0

    def test_deleting_a_pay_way_purges(self, purge):
        pay_way = PayWay.objects.create(provider_code="test_provider")
        purge.reset_mock()
        _pending_codes().clear()

        with transaction.atomic():
            pay_way.delete()

        assert _codes(purge) == ["pay_way"]


@real_commits
class TestCoalescing:
    def test_one_edit_across_three_languages_purges_once(self, purge):
        """Master plus three translation rows is ONE logical edit.
        Purging per row would SCAN Redis and POST to Nuxt four times."""
        with transaction.atomic():
            pay_way = PayWay.objects.create(provider_code="test_provider")
            for code, text in (
                ("el", "Ελληνικά"),
                ("en", "English"),
                ("de", "Deutsch"),
            ):
                pay_way.set_current_language(code)
                pay_way.description = text
                pay_way.save()

        assert _codes(purge) == ["pay_way"]

    def test_a_rolled_back_edit_purges_nothing(self, purge):
        """Purging inside the transaction would evict a still-valid
        entry and re-populate it from uncommitted state."""
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                PayWay.objects.create(provider_code="test_provider")
                raise RuntimeError("rolled back")

        assert purge.call_count == 0

    def test_a_commit_after_a_rollback_still_purges(self, purge):
        """Regression: scheduling only on the FIRST write of a
        transaction used "the pending set is non-empty" to mean "a
        callback is already queued". Django discards on_commit callbacks
        on rollback but nothing clears the set, so the rolled-back code
        stayed pending and suppressed scheduling for the NEXT
        transaction — which then purged nothing."""
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                PayWay.objects.create(provider_code="rolled_back")
                raise RuntimeError("rolled back")

        assert purge.call_count == 0

        with transaction.atomic():
            PayWay.objects.create(provider_code="committed")

        assert _codes(purge) == ["pay_way"]


@pytest.mark.django_db
class TestFailureIsolation:
    def test_a_failing_purge_does_not_break_the_write(self):
        """The write has already committed by the time ``_flush`` runs;
        an exception there has no caller and must not escape."""
        with mock.patch(
            "core.cache.service.CacheService.purge",
            side_effect=RuntimeError("redis down"),
        ):
            with transaction.atomic():
                pay_way = PayWay.objects.create(provider_code="test_provider")

        assert PayWay.objects.filter(pk=pay_way.pk).exists()

    def test_the_pending_set_is_cleared_after_a_failure(self):
        """Otherwise the stale codes ride into the next unrelated
        transaction on this thread, forever."""
        with mock.patch(
            "core.cache.service.CacheService.purge",
            side_effect=RuntimeError("redis down"),
        ):
            with transaction.atomic():
                PayWay.objects.create(provider_code="test_provider")

        assert _pending_codes() == set()


class TestWiring:
    def test_the_shipping_options_payload_is_invalidated_too(self):
        """``/api/v1/shipping/options`` embeds each option's eligible
        pay-ways, rendered as badges on the delivery step, and is cached
        under the ``shipping`` surface. ``CacheService.purge`` expands
        ``related`` itself, so assert the declaration rather than
        re-testing the expansion."""
        from core.cache.registry import expand_with_related, get_surface

        assert "shipping" in get_surface("pay_way").related
        assert "shipping" in expand_with_related(["pay_way"])

    def test_the_pay_way_surface_declares_its_models(self):
        """A surface with no ``invalidated_by`` is purge-on-demand only
        — the exact bug this closes."""
        from core.cache.registry import get_surface

        surface = get_surface("pay_way")

        assert "pay_way.PayWay" in surface.invalidated_by
        assert "pay_way.PayWayTranslation" in surface.invalidated_by

    def test_connecting_is_skipped_when_caching_is_disabled(self):
        """Nothing is cached, so nothing needs invalidating — and
        connecting anyway would make every factory-built row in the rest
        of the suite reach for a Redis client that is not there."""
        from django.db.models.signals import post_save

        disconnect_surface_invalidation()
        before = len(post_save.receivers)

        connect_surface_invalidation()  # DISABLE_CACHE is True here

        assert len(post_save.receivers) == before

        connect_surface_invalidation(force=True)
        assert len(post_save.receivers) > before

    def test_connecting_twice_does_not_duplicate_receivers(self):
        """``AppConfig.ready()`` can run more than once."""
        from django.db.models.signals import post_save

        connect_surface_invalidation(force=True)
        after_first = len(post_save.receivers)
        connect_surface_invalidation(force=True)

        assert len(post_save.receivers) == after_first

    def test_every_declared_model_resolves(self):
        """A typo in a model label silently disables invalidation for
        that surface, with nothing but a warning at boot."""
        from django.apps import apps

        from core.cache.registry import iter_surfaces

        for surface in iter_surfaces():
            for label in surface.invalidated_by:
                apps.get_model(label)  # raises LookupError on a typo


@pytest.mark.django_db
class TestBatching:
    """A commit only records its stale surfaces and queues one delayed
    purge; every commit inside the delay rides it. A catalogue import
    that commits once per product must cost one purge, not one per row.
    """

    def test_commits_in_separate_transactions_share_one_queued_purge(
        self, purge
    ):
        from core.cache import invalidation

        with mock.patch(
            "core.tasks.invalidate_cache_surfaces_task.apply_async"
        ) as queue:
            for code in ("first", "second", "third"):
                with transaction.atomic():
                    PayWay.objects.create(provider_code=code)

        queue.assert_called_once_with(
            countdown=invalidation.PURGE_DELAY_SECONDS
        )
        assert purge.call_count == 0, "nothing purges until the task runs"

        assert invalidation.run_queued_purge() == ["pay_way"]
        assert _codes(purge) == ["pay_way"]

    def test_the_task_takes_the_whole_set_once(self, purge):
        from core.cache import invalidation

        with mock.patch(
            "core.tasks.invalidate_cache_surfaces_task.apply_async"
        ):
            with transaction.atomic():
                PayWay.objects.create(provider_code="once")

        assert invalidation.run_queued_purge() == ["pay_way"]
        assert invalidation.run_queued_purge() == []
        assert purge.call_count == 1

    def test_a_commit_while_the_task_runs_queues_the_next_one(self, purge):
        """The flag is cleared before the set is taken, so an edit landing
        during a purge schedules another instead of being dropped."""
        from core.cache import invalidation

        with mock.patch(
            "core.tasks.invalidate_cache_surfaces_task.apply_async"
        ) as queue:
            with transaction.atomic():
                PayWay.objects.create(provider_code="before")
            invalidation.run_queued_purge()
            with transaction.atomic():
                PayWay.objects.create(provider_code="during")

        assert queue.call_count == 2
        assert invalidation.run_queued_purge() == ["pay_way"]

    def test_a_queueing_failure_does_not_break_the_write(self):
        with mock.patch(
            "django.core.cache.cache.add_to_set",
            side_effect=RuntimeError("redis down"),
        ):
            with transaction.atomic():
                pay_way = PayWay.objects.create(provider_code="redis_down")

        assert PayWay.objects.filter(pk=pay_way.pk).exists()
        assert _pending_codes() == set()


class TestIgnoredUpdateFields:
    """Every order decrements stock with ``save(update_fields=["stock",
    "updated_at"])``. Purging the product surface for that would empty a
    store's product cache on each sale."""

    def test_a_stock_only_save_leaves_the_product_surface_alone(self):
        from core.cache import invalidation

        with mock.patch.object(invalidation, "_mark_stale") as mark:
            receiver = invalidation._make_receiver(
                "products", frozenset({"stock", "updated_at"})
            )
            receiver(sender=None, update_fields=frozenset({"stock"}))
            receiver(
                sender=None, update_fields=frozenset({"stock", "updated_at"})
            )
        mark.assert_not_called()

    def test_any_other_field_or_a_full_save_invalidates(self):
        from core.cache import invalidation

        with mock.patch.object(invalidation, "_mark_stale") as mark:
            receiver = invalidation._make_receiver(
                "products", frozenset({"stock", "updated_at"})
            )
            receiver(sender=None, update_fields=frozenset({"stock", "price"}))
            receiver(sender=None, update_fields=None)
        assert mark.call_count == 2

    def test_the_product_surface_declares_the_stock_fields(self):
        from core.cache.registry import get_surface

        assert get_surface("products").ignored_update_fields == frozenset(
            {"stock", "updated_at"}
        )


class TestPageSurfacesAreWired:
    """The surfaces whose Nitro page renders are also cached at the
    Cloudflare edge purge on a merchant's edit, not only from the Cache
    tool — otherwise a price change waits out the page TTL."""

    @pytest.mark.parametrize(
        ("code", "label"),
        [
            ("products", "product.Product"),
            ("products", "product.ProductTranslation"),
            ("products", "product.ProductImage"),
            ("categories", "product.ProductCategoryTranslation"),
            ("blog", "blog.BlogPostTranslation"),
            ("page_config", "page_config.PageSection"),
            ("page_config", "page_config.NavigationLinkTranslation"),
            ("promotions", "promotion.PromotionTranslation"),
        ],
    )
    def test_declares(self, code, label):
        from core.cache.registry import get_surface

        assert label in get_surface(code).invalidated_by

    def test_user_generated_rows_stay_out(self):
        from core.cache.registry import iter_surfaces

        declared = {
            label for s in iter_surfaces() for label in s.invalidated_by
        }
        for label in (
            "product.ProductReview",
            "product.ProductFavourite",
            "product.ProductAlert",
            "blog.BlogComment",
            "promotion.PromotionRedemption",
        ):
            assert label not in declared
