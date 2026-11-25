"""Every session asks the server to probe a client that went quiet.

A session can hold an advisory lock across a call to another service (the
delivery lock of an email, a running task's store lock). If its client's
node is lost, nothing closes the socket, and the server holds the lock for
as long as it takes to notice: about half an hour with the server's own
probes, unless the session sets its own TCP keepalives. The test database's options are its
own (``tests/conftest.py``), so this opens a session with the options the
deployed settings give every connection.
"""

from __future__ import annotations

import psycopg
import pytest
from django.db import connection

from settings import database

pytestmark = pytest.mark.django_db

KEEPALIVES = (
    "tcp_keepalives_idle",
    "tcp_keepalives_interval",
    "tcp_keepalives_count",
)


def test_a_deployed_session_sets_its_tcp_keepalives():
    params = connection.get_connection_params()
    params["options"] = database._db_options["options"]

    with psycopg.connect(**params) as session:
        applied = dict(
            session.execute(
                "SELECT name, setting FROM pg_settings WHERE name = ANY(%s)",
                [list(KEEPALIVES)],
            ).fetchall()
        )

    assert applied == {
        "tcp_keepalives_idle": str(database.DB_TCP_KEEPALIVES_IDLE),
        "tcp_keepalives_interval": str(database.DB_TCP_KEEPALIVES_INTERVAL),
        "tcp_keepalives_count": str(database.DB_TCP_KEEPALIVES_COUNT),
    }
