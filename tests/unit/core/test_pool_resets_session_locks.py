"""A connection returned to the pool keeps no advisory lock.

Django's ``close()`` returns the server session to the pool. A session
lock taken before it (``tenant.celery.holding_store``) would otherwise
stay on a session that later serves other work.
"""

from __future__ import annotations

import pytest
from django.db import connection
from psycopg_pool import ConnectionPool

from settings.database import _release_session_locks

pytestmark = pytest.mark.django_db


def _advisory_locks(conn) -> int:
    return conn.execute(
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
        "AND pid = pg_backend_pid()"
    ).fetchone()[0]


@pytest.mark.parametrize(
    ("reset", "left"), [(_release_session_locks, 0), (None, 1)]
)
def test_a_returned_connection_drops_its_session_locks(reset, left):
    params = connection.get_connection_params()
    params.pop("pool", None)
    params["autocommit"] = True
    pool = ConnectionPool(
        kwargs=params, min_size=1, max_size=1, reset=reset, open=True
    )
    try:
        conn = pool.getconn()
        conn.execute(
            "SELECT pg_advisory_lock_shared(hashtextextended('probe', 0))"
        )
        pool.putconn(conn)

        with pool.connection() as again:
            assert _advisory_locks(again) == left
    finally:
        pool.close()
