"""The connections the app publishes on, and RabbitMQ blocking them (#509).

While RabbitMQ blocks publishers (a memory or disk alarm) it stops reading a
connection as soon as that connection publishes, and says so with
``connection.blocked`` on that connection alone. A connection that has not
published since the alarm started is told nothing, so every new one would
write one more message before it learned, and that message is delivered once
the alarm clears.

So the state is kept per process, not per connection:

- ``connection.blocked`` on any publishing connection holds this process off
  the broker for ``BROKER_BLOCKED_HOLD_SECONDS``. The publish waiting for its
  confirm ends at once, with ``amqp.exceptions.Blocked``: a recoverable
  connection error, so kombu drops that connection without the close
  handshake a blocked broker never answers, and retries on a new one.
- While the hold lasts, every publish on a publishing connection is refused
  before it writes, with ``BrokerBlocked``, which kombu does not retry. The
  retry above ends there, and so do the publishes that retry without end
  (worker events and heartbeats, control commands).
- ``connection.unblocked`` ends the hold. RabbitMQ sends it only on the
  connection it blocked, which kombu has usually dropped by then, so once
  the hold has passed the next publish first asks again with a message no
  queue receives: blocked again, the hold starts over and the real message
  is never written; confirmed, publishing resumes. A question that fails for
  another reason (a connection a restarted broker reset) leaves the hold
  passed, so kombu's retry asks again on a new connection.

A task published by a task the worker is running is not refused: a refusal
there loses it (``Task.retry`` turns a failed publish into
``Reject(requeue=False)``, and an ``acks_late`` message rejected that way is
dropped). It is written, and when the broker blocks it the publish waits for
its confirm until the alarm clears, as it did before #509, bounded by the
task's time limit (``waiting_through_blocks``).

Only the connections Celery opens through ``connection_for_write`` use this
transport. A worker's consuming connection keeps kombu's own: its reads must
not time out, or RabbitMQ redelivers every task the worker is running. For
the same reason a publishing connection never fails for heartbeats it
missed (``PublishingConnection``): a miss on the worker's event connection
reconnected the consuming one too (#721).
"""

import logging
import os
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import amqp
from amqp import spec
from amqp.exceptions import Blocked, ConnectionForced, MessageNacked
from django.conf import settings
from kombu.exceptions import OperationalError
from kombu.transport import pyamqp

from core.cache_backend import BurstLogger

logger = logging.getLogger(__name__)

#: The default exchange routes a message to the queue named by its routing
#: key, and RabbitMQ lets no client declare a queue whose name starts with
#: ``amq.``, so the question is dropped once the broker reads it.
PROBE_ROUTING_KEY = "amq.grooveshop.blocked-probe"


#: A remote control query answered by no worker because the broker blocks
#: publishers (``core.celery.GrooveShopControl``).
unanswered_query = BurstLogger(
    "Remote control query %r answered by no worker: RabbitMQ blocks"
    " publishers (%s similar answer(s) suppressed in the last %ss)",
    target=logger,
)


class BrokerBlocked(OperationalError):
    """This process publishes nothing while RabbitMQ blocks publishers."""


class _Hold:
    """Until when this process refuses to publish; ``None`` when not held.

    Module state, shared by every thread of the process and inherited by a
    forked worker child, which reads the same monotonic clock. ``asking``
    lets one thread at a time ask whether the broker still blocks.
    """

    until: float | None = None
    asking = threading.Lock()


_hold = _Hold()
_waiting: ContextVar[bool] = ContextVar("broker_waiting", default=False)


def _new_asking_lock() -> None:
    # A lock a parent thread held at the fork stays held in the child.
    _hold.asking = threading.Lock()


if hasattr(os, "register_at_fork"):  # POSIX: nothing forks on Windows
    os.register_at_fork(after_in_child=_new_asking_lock)


def broker_blocked() -> bool:
    """Whether this process is holding off a broker that blocked it."""
    return _hold.until is not None and time.monotonic() < _hold.until


def _refusal() -> BrokerBlocked:
    return BrokerBlocked(
        "RabbitMQ blocks publishers (a memory or disk alarm); "
        "nothing is published until it unblocks"
    )


def refuse_while_held() -> None:
    """Raise ``BrokerBlocked`` while this process holds off the broker."""
    if broker_blocked():
        raise _refusal()


@contextmanager
def waiting_through_blocks() -> Generator[None]:
    """Publish without the refusal, waiting out a block for the confirm.

    For a publish that must not fail because of a block: the message is
    written once and its confirm awaited until the broker unblocks.
    """
    token = _waiting.set(True)
    try:
        yield
    finally:
        _waiting.reset(token)


def _start_hold() -> None:
    _hold.until = time.monotonic() + settings.BROKER_BLOCKED_HOLD_SECONDS


def _on_blocked(reason: str) -> None:
    _start_hold()
    raise Blocked(reason)


def _on_unblocked() -> None:
    _hold.until = None


def _on_confirm(method: tuple[int, int], *args: Any) -> None:
    if method == spec.Basic.Nack:
        raise MessageNacked()


class PublishingChannel(pyamqp.Channel):
    """A channel that writes nothing while the broker blocks this process."""

    def basic_publish_confirm(self, *args: Any, **kwargs: Any) -> Any:
        if _waiting.get():
            return self._publish_through_block(*args, **kwargs)
        self._wait_out_hold()
        return super().basic_publish_confirm(*args, **kwargs)

    def _wait_out_hold(self) -> None:
        if _hold.until is None:
            return
        refuse_while_held()
        if not _hold.asking.acquire(blocking=False):
            raise _refusal()  # another thread is asking
        passed = _hold.until
        try:
            # The hold passed without ``connection.unblocked``. Ask with a
            # message no queue receives, holding the other publishes of this
            # process (and kombu's retry, if the answer is a block) meanwhile.
            _start_hold()
            try:
                super().basic_publish_confirm(
                    amqp.Message(b""),
                    exchange="",
                    routing_key=PROBE_ROUTING_KEY,
                )
            except Blocked:
                raise
            except BaseException:
                # No answer about the block: let kombu's retry ask again.
                _hold.until = passed
                raise
            _hold.until = None
        finally:
            _hold.asking.release()

    def _publish_through_block(self, *args: Any, **kwargs: Any) -> Any:
        written = self.connection.bytes_sent
        try:
            return super().basic_publish_confirm(*args, **kwargs)
        except Blocked:
            # ``bytes_sent`` counts socket writes (``Confirm.Select``
            # included), not message bytes. RabbitMQ sends
            # ``connection.blocked`` after a publish it holds back, so this
            # only guards a block that arrives before any write; then
            # nothing was sent and kombu's retry is safe.
            if self.connection.bytes_sent == written:
                raise
        # Written, and held back by the broker until its alarm clears: wait
        # for the confirm on this connection, which the broker unblocks.
        while True:
            try:
                return self.wait(
                    [spec.Basic.Ack, spec.Basic.Nack], callback=_on_confirm
                )
            except TimeoutError:
                continue  # ``read_timeout`` passed; the broker still blocks


class PublishingConnection(pyamqp.Connection):
    """A py-amqp connection that learns of a block and bounds its reads.

    Every publish is confirmed: a block is noticed while the confirm is
    awaited, and only ``basic_publish_confirm`` waits out the hold.
    ``read_timeout`` (``BROKER_PUBLISH_READ_TIMEOUT``) bounds any reply the
    broker never sends, such as the confirm of a connection it blocked
    without saying so.

    Missed heartbeats never fail it (#721). Nothing reads a publishing
    connection between publishes, so what it received says nothing about
    the broker. A worker opens its event dispatcher's connection with
    ``broker_heartbeat`` and its event loop checks that connection's
    heartbeats, but reads only the socket the dispatcher started with.
    After a block kombu replaces that socket and, during the hold, nothing
    publishes on the new one, so nothing reads it: once two intervals
    passed the loop raised "Too many heartbeats missed", restarted the
    consumer, and RabbitMQ redelivered every ``acks_late`` task the worker
    was running. The heartbeats are still sent, and RabbitMQ still sends
    its own, which is how it closes a connection kombu dropped while the
    broker was not reading it. A dead broker is still found: by the write
    and the confirm of the next publish, and by the consuming connection's
    own heartbeats.
    """

    Channel = PublishingChannel

    def __init__(self, **kwargs: Any) -> None:
        if settings.BROKER_PUBLISH_READ_TIMEOUT:
            kwargs.setdefault(
                "read_timeout", settings.BROKER_PUBLISH_READ_TIMEOUT
            )
        super().__init__(
            **{
                **kwargs,
                "confirm_publish": True,
                "on_blocked": _on_blocked,
                "on_unblocked": _on_unblocked,
            }
        )

    def heartbeat_tick(self, rate: int = 2) -> None:
        """Send a heartbeat when one is due; never fail for one missed."""
        try:
            super().heartbeat_tick(rate=rate)
        except ConnectionForced:
            # Raised only when nothing was received for two intervals,
            # after the heartbeat due was sent.
            return


class PublishingTransport(pyamqp.Transport):
    """kombu's py-amqp transport, on ``PublishingConnection``."""

    Connection = PublishingConnection
