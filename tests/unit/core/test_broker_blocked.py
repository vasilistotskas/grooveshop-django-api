"""A broker that blocks publishers gets one message from a process, not more.

``FakeBroker`` speaks just enough AMQP 0-9-1 for Celery to publish to it, and
blocks like RabbitMQ under a memory alarm: when a connection publishes, it
sends ``connection.blocked`` on that connection and reads nothing more from
it. A connection that has not published is told nothing. The tests drive
Celery's own publish paths (``send_task`` with its retry policy, as beat and
the request path publish; ``Producer.publish(retry=True)`` with no policy, as
worker events, heartbeats and control commands publish) through
``GrooveShopCelery.connection_for_write`` to it, and count what it received.
"""

from __future__ import annotations

import contextlib
import importlib
import socket
import struct
import threading
import time
from collections.abc import Callable, Iterator
from unittest import mock

import pytest
from amqp import spec
from amqp.exceptions import Blocked
from amqp.serialization import dumps, loads
from celery.exceptions import Retry
from django.core.exceptions import ImproperlyConfigured
from kombu.transport.pyamqp import Transport

from core import broker, celery_app
from core.broker import PROBE_ROUTING_KEY, BrokerBlocked
from core.celery import GrooveShopCelery
from core.retry import TRANSIENT_NETWORK_ERRORS

_FRAME_END = b"\xce"
_METHOD, _HEADER, _BODY = 1, 2, 3

#: What each synchronous method the client sends is answered with.
_REPLIES = {
    spec.Connection.Open: spec.Connection.OpenOk,
    spec.Channel.Open: spec.Channel.OpenOk,
    spec.Channel.Close: spec.Channel.CloseOk,
    spec.Confirm.Select: spec.Confirm.SelectOk,
    spec.Exchange.Declare: spec.Exchange.DeclareOk,
    spec.Queue.Bind: spec.Queue.BindOk,
}

#: Longer than any test needs, so a publish that would wait without end
#: fails the test instead of hanging it.
_BOUND_SECONDS = 10.0


class FakeBroker:
    """An AMQP server that confirms publishes until ``block()`` is called.

    A blocked connection reads nothing more until ``unblock()``, which sends
    it ``connection.unblocked`` and the confirm it held back, as RabbitMQ does
    once its alarm clears.
    """

    def __init__(self) -> None:
        self.blocking = False
        self._released = threading.Event()
        self.published: list[str] = []
        self.connections = 0
        self._server = socket.create_server(("127.0.0.1", 0))
        self._sockets: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    @property
    def url(self) -> str:
        host, port = self._server.getsockname()
        return f"amqp://guest:guest@{host}:{port}//"

    def block(self) -> None:
        self._released.clear()
        self.blocking = True

    def unblock(self) -> None:
        self.blocking = False
        self._released.set()

    def restart(self) -> None:
        """Drop every open connection, as a restarted broker does."""
        for sock in self._sockets:
            with contextlib.suppress(OSError):  # the client may have gone
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()
        self._sockets.clear()

    def close(self) -> None:
        self.restart()
        self._server.close()
        self._released.set()

    def _accept(self) -> None:
        while True:
            try:
                sock, _ = self._server.accept()
            except OSError:
                return
            self.connections += 1
            self._sockets.append(sock)
            threading.Thread(
                target=self._serve, args=(sock,), daemon=True
            ).start()

    def _serve(self, sock: socket.socket) -> None:
        reader = sock.makefile("rb")
        try:
            reader.read(8)  # the protocol header
            self._send(
                sock,
                0,
                spec.Connection.Start,
                "ooFSS",
                (
                    0,
                    9,
                    {"capabilities": {"connection.blocked": True}},
                    "AMQPLAIN PLAIN",
                    "en_US",
                ),
            )
            delivery_tags: dict[int, int] = {}
            while True:
                channel, method, args = self._read_method(reader)
                if method == spec.Connection.StartOk:
                    self._send(sock, 0, spec.Connection.Tune, "BlB", (0, 0, 0))
                elif method == spec.Connection.Close:
                    self._send(sock, 0, spec.Connection.CloseOk)
                    return
                elif method == spec.Queue.Declare:
                    queue = loads("BsbbbbbF", args, 0)[0][1]
                    self._send(
                        sock,
                        channel,
                        spec.Queue.DeclareOk,
                        "sll",
                        (queue, 0, 0),
                    )
                elif method == spec.Basic.Publish:
                    routing_key = loads("Bssbb", args, 0)[0][2]
                    self._read_content(reader)
                    self.published.append(routing_key)
                    if self.blocking:
                        # RabbitMQ stops reading a connection it blocks.
                        self._send(sock, 0, spec.Connection.Blocked)
                        self._released.wait()
                        self._send(sock, 0, spec.Connection.Unblocked)
                    delivery_tags[channel] = delivery_tags.get(channel, 0) + 1
                    self._send(
                        sock,
                        channel,
                        spec.Basic.Ack,
                        "Lb",
                        (delivery_tags[channel], False),
                    )
                elif method in _REPLIES:
                    self._send(sock, channel, _REPLIES[method])
        except OSError, struct.error:
            return

    @staticmethod
    def _read_frame(reader) -> tuple[int, int, bytes]:
        frame_type, channel, size = struct.unpack(">BHI", reader.read(7))
        payload = reader.read(size)
        reader.read(1)
        return frame_type, channel, payload

    def _read_method(self, reader) -> tuple[int, tuple[int, int], bytes]:
        while True:
            frame_type, channel, payload = self._read_frame(reader)
            if frame_type == _METHOD:
                return channel, struct.unpack(">HH", payload[:4]), payload[4:]

    def _read_content(self, reader) -> None:
        _, _, header = self._read_frame(reader)
        (size,) = struct.unpack(">Q", header[4:12])
        while size > 0:
            size -= len(self._read_frame(reader)[2])

    @staticmethod
    def _send(sock, channel, method, argsig="", args=()) -> None:
        payload = struct.pack(">HH", *method) + dumps(argsig, args)
        sock.sendall(
            struct.pack(">BHI", _METHOD, channel, len(payload))
            + payload
            + _FRAME_END
        )


@pytest.fixture(autouse=True)
def no_hold() -> Iterator[None]:
    broker._hold.until = None
    yield
    broker._hold.until = None


@pytest.fixture
def fake_broker() -> Iterator[FakeBroker]:
    server = FakeBroker()
    yield server
    server.close()


@pytest.fixture
def connection(fake_broker: FakeBroker, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = False  # send_task warns otherwise
    conn = celery_app.connection_for_write(fake_broker.url)
    yield conn
    conn.collect()  # a blocked broker never answers a close


def _bounded(call: Callable[[], object]) -> BaseException | None:
    """What ``call`` raised, failing the test if it is still running."""
    raised: list[BaseException] = []

    def run() -> None:
        try:
            call()
        except BaseException as exc:
            raised.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(_BOUND_SECONDS)
    assert not worker.is_alive(), f"still publishing after {_BOUND_SECONDS} s"
    return (raised or [None])[0]


def _send_task(connection) -> Callable[[], object]:
    """A task publish as beat and the request path make it: Celery's
    ``send_task`` with the publish retry policy from the settings."""
    producer = celery_app.amqp.Producer(connection, auto_declare=False)
    return lambda: celery_app.send_task("demo.task", producer=producer)


def _publish_retrying_forever(connection) -> Callable[[], object]:
    """An event, heartbeat or control command: ``retry=True`` and no
    policy, so kombu retries a connection error without end."""
    producer = celery_app.amqp.Producer(connection, auto_declare=False)
    return lambda: producer.publish(
        {"event": 1}, routing_key="events", retry=True
    )


def test_a_healthy_broker_receives_every_publish(fake_broker, connection):
    assert _bounded(_send_task(connection)) is None
    assert _bounded(_publish_retrying_forever(connection)) is None

    assert fake_broker.published == ["celery", "task.sent", "events"]


def test_a_blocked_publish_ends_at_once_and_nothing_more_is_written(
    fake_broker, connection
):
    fake_broker.block()

    started = time.monotonic()
    first = _bounded(_send_task(connection))
    took = time.monotonic() - started
    later = [
        _bounded(_send_task(connection)),
        _bounded(_publish_retrying_forever(connection)),
        _bounded(_send_task(connection)),
    ]

    assert isinstance(first, BrokerBlocked), first
    assert took < 2, f"the blocked publish took {took:.1f} s"
    assert all(isinstance(exc, BrokerBlocked) for exc in later), later
    assert fake_broker.published == ["celery"], (
        "only the publish that found out wrote a message"
    )
    assert fake_broker.connections == 2, (
        "kombu dropped the blocked connection and retried once on a new one"
    )


def test_a_publish_retrying_forever_is_not_a_reconnect_loop(
    fake_broker, connection
):
    fake_broker.block()

    raised = _bounded(_publish_retrying_forever(connection))

    assert isinstance(raised, BrokerBlocked), raised
    assert fake_broker.published == ["events"]
    assert fake_broker.connections == 2


def test_after_the_hold_it_asks_with_a_message_no_queue_receives(
    fake_broker, connection, settings
):
    settings.BROKER_BLOCKED_HOLD_SECONDS = 0.2
    fake_broker.block()
    _bounded(_send_task(connection))
    time.sleep(0.3)

    raised = _bounded(_send_task(connection))

    assert isinstance(raised, BrokerBlocked), raised
    assert fake_broker.published == ["celery", PROBE_ROUTING_KEY]
    assert broker.broker_blocked(), "the answer started the hold again"


def test_once_the_broker_answers_publishing_resumes(
    fake_broker, connection, settings
):
    settings.BROKER_BLOCKED_HOLD_SECONDS = 0.2
    fake_broker.block()
    _bounded(_send_task(connection))
    fake_broker.unblock()
    time.sleep(0.3)

    assert _bounded(_send_task(connection)) is None
    assert _bounded(_send_task(connection)) is None

    assert fake_broker.published == [
        "celery",
        PROBE_ROUTING_KEY,
        "celery",
        "task.sent",
        "celery",
        "task.sent",
    ]
    assert not broker.broker_blocked()


def test_connection_unblocked_ends_the_hold():
    with pytest.raises(Blocked):
        broker._on_blocked("connection blocked, see broker logs")
    assert broker.broker_blocked()

    broker._on_unblocked()

    assert not broker.broker_blocked()


def test_the_block_is_retried_on_a_new_connection_and_the_refusal_is_not():
    """``connection.blocked`` raises a recoverable connection error, so kombu
    drops the blocked connection without a close handshake (also when it
    arrives during a close, where kombu ignores connection errors); the
    refusal is a ``KombuError``, which kombu does not retry and
    ``dispatch_on_commit`` keeps as a ``DeferredDispatch``."""
    with pytest.raises(Transport.recoverable_connection_errors):
        broker._on_blocked("connection blocked, see broker logs")

    refusal = BrokerBlocked("blocked")
    assert not isinstance(refusal, Transport.connection_errors)
    assert isinstance(refusal, TRANSIENT_NETWORK_ERRORS)


def test_only_publishing_connections_use_the_transport():
    with celery_app.connection_for_write() as writing:
        assert writing.transport_cls is broker.PublishingTransport
    # Beat's banner opens one this way, passing ``transport=None``.
    with celery_app.connection() as default:
        assert default.transport_cls is broker.PublishingTransport
    with celery_app.connection_for_read() as reading:
        assert reading.transport_cls is not broker.PublishingTransport
        assert "read_timeout" not in reading.transport_options


def test_a_publishing_connection_confirms_every_publish():
    connection = broker.PublishingConnection(
        host="127.0.0.1:1", confirm_publish=False
    )

    assert connection.confirm_publish is True
    assert connection.on_blocked is broker._on_blocked
    assert connection.on_unblocked is broker._on_unblocked


def test_a_question_that_fails_for_another_reason_asks_again(
    fake_broker, connection, settings
):
    """After a broker restart the question meets a connection the broker
    reset. That says nothing about a block, so kombu's retry asks again on
    a new connection instead of meeting a new hold."""
    settings.BROKER_BLOCKED_HOLD_SECONDS = 0.2
    fake_broker.block()
    _bounded(_send_task(connection))
    fake_broker.unblock()
    fake_broker.restart()
    time.sleep(0.3)

    assert _bounded(_send_task(connection)) is None

    assert fake_broker.published[-3:] == [
        PROBE_ROUTING_KEY,
        "celery",
        "task.sent",
    ]
    assert not broker.broker_blocked()


@pytest.fixture
def pooled_app(fake_broker: FakeBroker, settings) -> Iterator[GrooveShopCelery]:
    """An app publishing through its own producer pool, as the request path
    and a worker's tasks do (``send_task`` without ``producer=``)."""
    app = GrooveShopCelery("pooled", set_as_current=False)
    # Not ``broker_url``, which $CELERY_BROKER_URL overrides.
    app.conf.broker_write_url = fake_broker.url
    app.conf.task_publish_retry_policy = (
        settings.CELERY_TASK_PUBLISH_RETRY_POLICY
    )
    yield app
    fake_broker.close()  # a blocked broker never answers the pool's close
    app.close()


def test_the_producer_pool_refuses_without_taking_a_connection(
    fake_broker, pooled_app
):
    fake_broker.block()

    raised = [
        _bounded(lambda: pooled_app.send_task("demo.task")) for _ in range(4)
    ]

    assert all(isinstance(exc, BrokerBlocked) for exc in raised), raised
    assert fake_broker.published == ["celery"]
    assert fake_broker.connections == 2, "the blocked one and kombu's retry"


def test_a_task_retry_during_a_block_waits_and_is_published_once(
    fake_broker, pooled_app
):
    """``Task.retry`` turns a failed publish into ``Reject(requeue=False)``,
    which drops an ``acks_late`` message. From a task the worker is running
    the publish is never refused: it is written once and waits for its
    confirm until the broker unblocks."""

    @pooled_app.task(bind=True, name="demo.retrying")
    def retrying(self):
        raise AssertionError("not run here")

    def retry_as_the_worker_does() -> None:
        # The request stack is per thread, so it is pushed in this one.
        retrying.push_request(
            id="retrying-1",
            args=[],
            kwargs={},
            retries=0,
            called_directly=False,
        )
        try:
            outcome.append(retrying.retry(countdown=1, throw=False))
        finally:
            retrying.pop_request()

    with pytest.raises(Blocked):
        broker._on_blocked("an earlier publish of this process was blocked")
    fake_broker.block()
    threading.Timer(0.5, fake_broker.unblock).start()
    outcome: list[object] = []
    started = time.monotonic()
    with mock.patch.object(
        GrooveShopCelery,
        "current_worker_task",
        new_callable=mock.PropertyMock,
        return_value=retrying,
    ):
        raised = _bounded(retry_as_the_worker_does)

    assert raised is None, raised
    assert isinstance(outcome[0], Retry), outcome
    assert time.monotonic() - started >= 0.5, "it waited for the broker"
    assert fake_broker.published == ["celery"], "written once"
    assert not broker.broker_blocked(), "connection.unblocked ended the hold"


@pytest.fixture
def settings_module(monkeypatch) -> Iterator[Callable[..., object]]:
    import settings.celery as module

    def reload(**env: str) -> object:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(module)

    yield reload
    monkeypatch.undo()
    importlib.reload(module)


@pytest.mark.parametrize("hold", ["0", "-1", "nan"])
def test_a_hold_must_be_longer_than_nothing(settings_module, hold):
    """With no hold, worker events and heartbeats, which kombu retries
    without end, would reconnect to a blocked broker in a loop."""
    with pytest.raises(ImproperlyConfigured, match="BROKER_BLOCKED_HOLD"):
        settings_module(BROKER_BLOCKED_HOLD_SECONDS=hold)
