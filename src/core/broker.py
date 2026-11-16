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
  is never written; confirmed, publishing resumes.

Only the connections Celery opens through ``connection_for_write`` use this
transport. A worker's consuming connection keeps kombu's own: its reads must
not time out, or RabbitMQ redelivers every task the worker is running.
"""

import time
from typing import Any

import amqp
from amqp.exceptions import Blocked
from django.conf import settings
from kombu.exceptions import OperationalError
from kombu.transport import pyamqp

#: The default exchange routes a message to the queue named by its routing
#: key, and RabbitMQ lets no client declare a queue whose name starts with
#: ``amq.``, so the question is dropped once the broker reads it.
PROBE_ROUTING_KEY = "amq.grooveshop.blocked-probe"


class BrokerBlocked(OperationalError):
    """This process publishes nothing while RabbitMQ blocks publishers."""


class _Hold:
    """Until when this process refuses to publish; ``None`` when not held.

    Module state, shared by every thread of the process and inherited by a
    forked worker child, which reads the same monotonic clock.
    """

    until: float | None = None


_hold = _Hold()


def broker_blocked() -> bool:
    """Whether this process is holding off a broker that blocked it."""
    return _hold.until is not None and time.monotonic() < _hold.until


def refuse_while_held() -> None:
    """Raise ``BrokerBlocked`` while this process holds off the broker."""
    if broker_blocked():
        raise BrokerBlocked(
            "RabbitMQ blocks publishers (a memory or disk alarm); "
            "nothing is published until it unblocks"
        )


def _start_hold() -> None:
    _hold.until = time.monotonic() + settings.BROKER_BLOCKED_HOLD_SECONDS


def _on_blocked(reason: str) -> None:
    _start_hold()
    raise Blocked(reason)


def _on_unblocked() -> None:
    _hold.until = None


class PublishingChannel(pyamqp.Channel):
    """A channel that writes nothing while the broker blocks this process."""

    def basic_publish_confirm(self, *args: Any, **kwargs: Any) -> Any:
        self._wait_out_hold()
        return super().basic_publish_confirm(*args, **kwargs)

    def _wait_out_hold(self) -> None:
        if _hold.until is None:
            return
        refuse_while_held()
        # The hold passed without ``connection.unblocked``. Ask with a
        # message no queue receives, holding the other publishes of this
        # process (and kombu's retry, if the answer is a block) meanwhile.
        _start_hold()
        super().basic_publish_confirm(
            amqp.Message(b""), exchange="", routing_key=PROBE_ROUTING_KEY
        )
        _hold.until = None


class PublishingConnection(pyamqp.Connection):
    """A py-amqp connection that learns of a block and bounds its reads.

    Every publish is confirmed: a block is noticed while the confirm is
    awaited, and only ``basic_publish_confirm`` waits out the hold.
    ``read_timeout`` (``BROKER_PUBLISH_READ_TIMEOUT``) bounds any reply the
    broker never sends, such as the confirm of a connection it blocked
    without saying so.
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


class PublishingTransport(pyamqp.Transport):
    """kombu's py-amqp transport, on ``PublishingConnection``."""

    Connection = PublishingConnection
