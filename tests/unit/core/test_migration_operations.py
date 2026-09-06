"""``BackfillNullStringsToEmpty``, exercised rather than reviewed.

Two migrations in this audit were signed off by review and still had
defects that only running them exposed — a router the raw SQL bypassed,
a server-side cursor that did not survive a per-batch commit. This
operation is shared by four migrations and will be reused by the release
that adds the ``NOT NULL`` constraints, so its edges get a test:

* the pk-range walk covers rows beyond the first batch;
* ``COALESCE`` per column leaves a populated sibling column alone;
* a second run changes nothing;
* an empty table is a no-op rather than an error;
* the router is honoured, because raw SQL does not honour it for free.
"""

from __future__ import annotations

import pytest
from django.apps import apps as global_apps
from django.db import connection
from django.db.migrations.state import ProjectState
from django.test import override_settings

from core.db.migration_operations import BackfillNullStringsToEmpty
from search.models import SearchQuery

pytestmark = pytest.mark.django_db


def _run(fields, batch_size=1000):
    state = ProjectState.from_apps(global_apps)
    operation = BackfillNullStringsToEmpty(
        "searchquery", fields, batch_size=batch_size
    )
    with connection.schema_editor(atomic=False) as editor:
        operation.database_forwards("search", editor, state, state)


def _make(count, **overrides):
    """Rows carrying the OLD spelling.

    The model now defaults these columns to "", so the NULLs have to be
    written past it — which is also what the real rows look like: they
    predate the default.
    """
    rows = [
        SearchQuery.objects.create(
            query=f"q{i}",
            content_type="product",
            results_count=0,
            estimated_total_hits=0,
            **overrides,
        )
        for i in range(count)
    ]
    ids = [row.pk for row in rows]
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE search_query SET language_code = NULL WHERE id = ANY(%s)",
            [ids],
        )
    return ids


def test_backfill_reaches_rows_past_the_first_batch():
    ids = _make(7)

    _run(["language_code"], batch_size=2)

    values = set(
        SearchQuery.objects.filter(pk__in=ids).values_list(
            "language_code", flat=True
        )
    )
    assert values == {""}, (
        "The pk-range walk left rows behind. With batch_size=2 over 7 "
        "rows it must take four steps, not one."
    )


def test_backfill_leaves_a_populated_sibling_column_alone():
    ids = _make(3, session_key="keep-me")

    _run(["language_code", "session_key"])

    rows = SearchQuery.objects.filter(pk__in=ids)
    assert {row.language_code for row in rows} == {""}
    assert {row.session_key for row in rows} == {"keep-me"}, (
        "A multi-column backfill must COALESCE each column on its own. "
        "Assigning '' outright would blank the columns that were fine."
    )


def test_backfill_is_idempotent():
    ids = _make(4)

    _run(["language_code"])
    _run(["language_code"])

    assert set(
        SearchQuery.objects.filter(pk__in=ids).values_list(
            "language_code", flat=True
        )
    ) == {""}


def test_backfill_on_an_empty_table_is_a_no_op():
    SearchQuery.objects.all().delete()

    # MIN(pk) is NULL here; the walk must return rather than compare
    # against None.
    _run(["language_code"])


class _DenyEverything:
    def allow_migrate(self, db, app_label, **hints):
        return False


@override_settings(DATABASE_ROUTERS=[_DenyEverything()])
def test_backfill_honours_the_database_router():
    ids = _make(2)

    _run(["language_code"])

    assert (
        SearchQuery.objects.filter(
            pk__in=ids, language_code__isnull=True
        ).count()
        == 2
    ), (
        "Raw SQL bypasses the router, so the operation has to make the "
        "check Django's own operations make. Without it, migrating the "
        "public schema reaches into a tenant app's table."
    )


def test_batch_size_below_one_is_refused_at_construction():
    """A zero step never advances the walk.

    Caught in ``__init__`` rather than at run time, because the run time
    is inside the PreSync hook: the loop would spin until the Job's
    ``activeDeadlineSeconds`` killed it, and the deploy would fail with a
    timeout instead of a reason.
    """
    with pytest.raises(ValueError, match="batch_size must be >= 1"):
        BackfillNullStringsToEmpty(
            "searchquery", ["language_code"], batch_size=0
        )


def test_backfill_targets_the_schema_qualified_table():
    """`search_path` is `"<schema>", public` while a tenant migrates.

    An unqualified table name silently resolves to the public schema's
    copy when the tenant's own is missing, rewriting the wrong rows once
    per tenant. Assert the emitted SQL carries the schema.
    """
    executed = []
    real_cursor = connection.cursor

    class _Recording:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def execute(self, sql, params=None):
            executed.append(sql)
            return self._inner.execute(sql, params)

    class _Wrapper:
        def __enter__(self):
            self._ctx = real_cursor()
            return _Recording(self._ctx.__enter__())

        def __exit__(self, *exc):
            return self._ctx.__exit__(*exc)

    _make(2)
    connection.cursor = lambda: _Wrapper()
    try:
        _run(["language_code"])
    finally:
        connection.cursor = real_cursor

    updates = [sql for sql in executed if sql.startswith("UPDATE")]
    assert updates, "the walk never issued an UPDATE"
    assert all(
        f'"{connection.schema_name}"."search_query"' in sql for sql in updates
    ), f"UPDATE must name the schema explicitly; got {updates!r}"
