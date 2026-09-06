"""``SortableModel`` appended positions without ever holding a lock.

``save()`` read the current maximum through
``get_ordering_queryset().select_for_update()`` under the comment "Lock
the table to prevent race conditions". Django DROPS the lock when the
queryset is consumed by ``aggregate()`` — Postgres forbids ``FOR UPDATE``
alongside an aggregate — so the statement that actually ran was a plain
``SELECT MAX("sort_order")``. Row locks could not have fixed it in any
case: two concurrent creates contend over a row that does not exist yet,
and READ COMMITTED row locks do not prevent phantoms.

The duplicate that results is not cosmetic. ``move_up`` looked up its
neighbour with ``get(sort_order=...)``, so two rows sharing a position
made the admin's move-up action raise ``MultipleObjectsReturned``.
"""

from __future__ import annotations

import threading
import zlib

import pytest
from django.db import connection, connections, reset_queries

from blog.factories.tag import BlogTagFactory
from blog.models.tag import BlogTag

pytestmark = pytest.mark.django_db


class TestTheAppendTakesARealLock:
    def test_assigning_a_position_locks_before_reading_the_maximum(
        self, settings
    ):
        """Deterministic counterpart to the threaded test below.

        The old code issued no lock on this path at all — the aggregate
        carried no ``FOR UPDATE`` — so the assertion that pins the fix
        is that a lock statement is issued, and issued first.
        """
        settings.DEBUG = True
        reset_queries()

        BlogTag().save()

        statements = [q["sql"] for q in connection.queries]
        lock_at = next(
            i
            for i, sql in enumerate(statements)
            if "pg_advisory_xact_lock" in sql
        )
        max_at = next(
            i for i, sql in enumerate(statements) if 'MAX("blog_blogtag"' in sql
        )
        assert lock_at < max_at, "the maximum was read before the lock"

    def test_the_lock_key_is_per_schema_and_per_model(self, settings):
        """Advisory locks are per DATABASE and every tenant shares one.

        A key without the schema would make one store's appends block
        another's; a key without the model would serialise every
        sortable model in the platform against every other.
        """
        settings.DEBUG = True
        reset_queries()

        BlogTag().save()

        (lock_sql,) = [
            q["sql"]
            for q in connection.queries
            if "pg_advisory_xact_lock" in q["sql"]
        ]
        scope = f"{connection.schema_name}:blog.blogtag"
        assert str(zlib.crc32(scope.encode())) in lock_sql
        assert BlogTag._meta.label_lower == "blog.blogtag"


@pytest.mark.django_db(transaction=True)
class TestConcurrentAppendsDoNotCollide:
    def test_parallel_creates_get_distinct_positions(self):
        """Two threads, one barrier, the real database.

        Before the fix this produced ``sort_order == [0, 0]`` on two runs
        out of three.
        """
        BlogTag.objects.all().delete()
        ready = threading.Barrier(2)
        errors: list[Exception] = []

        def create_one():
            try:
                ready.wait(timeout=10)
                BlogTag().save()
            except Exception as exc:
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=create_one) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert errors == []
        orders = sorted(BlogTag.objects.values_list("sort_order", flat=True))
        assert orders == [0, 1], orders


class TestMoveUpSurvivesPositionsAlreadyStoredTwice:
    """Repairing the append does not repair rows already stored badly."""

    def test_a_shared_position_no_longer_raises(self):
        first = BlogTagFactory(active=True)
        second = BlogTagFactory(active=True)
        mover = BlogTagFactory(active=True)
        BlogTag.objects.filter(pk__in=[first.pk, second.pk]).update(
            sort_order=1
        )
        BlogTag.objects.filter(pk=mover.pk).update(sort_order=2)
        mover.refresh_from_db()

        mover.move_up()

        mover.refresh_from_db()
        assert mover.sort_order == 1

    def test_a_gap_is_stepped_over_rather_than_ignored(self):
        """``get(sort_order=self.sort_order - 1)`` silently did nothing.

        It caught ``DoesNotExist`` and passed, so an item with a gap
        below it could never be moved up at all.
        """
        below = BlogTagFactory(active=True)
        mover = BlogTagFactory(active=True)
        BlogTag.objects.filter(pk=below.pk).update(sort_order=0)
        BlogTag.objects.filter(pk=mover.pk).update(sort_order=5)
        mover.refresh_from_db()

        mover.move_up()

        mover.refresh_from_db()
        below.refresh_from_db()
        assert (mover.sort_order, below.sort_order) == (0, 5)
