"""An advisory lock held across a call to another service.

A task that sends something once (a mail, an event to an API) can have two
copies running at the same moment, and the check of "was it sent?" and the
send must then be one step. A row lock or a transaction-scoped lock would
need a transaction open across the call, and
``idle_in_transaction_session_timeout`` ends a transaction left idle that
long. So the lock is the session's: taken outside any transaction, released
when the block ends, and gone with the session if the process dies, which
lets the copy that waits take over: at once when the process ends, and
within about a minute when its node is lost without closing the socket
(the TCP keepalives every session sets, ``settings.database``).
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager

from django.db import Error, OperationalError, connection
from psycopg.errors import LockNotAvailable, QueryCanceled

logger = logging.getLogger(__name__)


class LockWaitRanOut(OperationalError):
    """Another session held the lock for longer than this one may wait.

    Its own ``lock_timeout`` or ``statement_timeout`` ended the wait. The
    holder is still at work, so the caller's next attempt can find the work
    done: a task retries it. Raised only while taking the lock, never by the
    block it guards, so a timeout inside the block is not mistaken for one.
    """


def _take(sql: str, key: str) -> None:
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, [key])
    except OperationalError as error:
        if isinstance(error.__cause__, QueryCanceled | LockNotAvailable):
            raise LockWaitRanOut(key) from error
        raise


@contextmanager
def held_across_calls(key: str) -> Generator[None]:
    """Hold an exclusive advisory lock on *key* for the block.

    Another holder makes this wait, for at most the connection's
    ``lock_timeout`` or ``statement_timeout``: a longer wait raises
    ``LockWaitRanOut``. Advisory locks are per database, which every
    schema shares, so *key* names what it guards in a way no other store's
    key can repeat.

    Inside a transaction (a task applied eagerly, or called within one) the
    lock is the transaction's and ends with it, so it is never left on a
    pooled connection. An unlock that fails means the session broke, and
    the lock went with it.
    """
    if connection.in_atomic_block:
        _take("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", key)
        yield
        return
    _take("SELECT pg_advisory_lock(hashtextextended(%s, 0))", key)
    try:
        yield
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                    [key],
                )
        except Error:
            logger.warning(
                "The session holding the lock %s broke; the lock went with it",
                key,
            )
