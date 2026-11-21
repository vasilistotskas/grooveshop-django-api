"""A broker that stops answering cannot hold a publish without end (#509).

A broker can stop answering a publishing connection without saying why: the
publish confirm never comes, nor the ``Close-Ok`` kombu waits for when it
closes the connection after a failed publish. Both come down to
``Transport.read_frame()`` on a blocking socket, with no timeout of their own.
``core.broker.PublishingConnection`` bounds them with a read timeout
(``BROKER_PUBLISH_READ_TIMEOUT``). What it does when RabbitMQ does say it
blocked the connection is in ``test_broker_blocked.py``.

The silent peer behaves like that broker: it accepts the connection, then
neither reads nor writes. The socket is opened by py-amqp with the options a
publishing connection has, as ``amqp.Connection.connect`` opens it; the AMQP
handshake that would follow is left out, since a silent peer never answers it.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator

import pytest
from amqp.transport import TCPTransport
from kombu.transport.pyamqp import Transport

from core.broker import PublishingConnection

linux_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "py-amqp packs the socket timeout as a POSIX timeval, so the app "
        "sets it on Linux only (src/settings/celery.py)"
    ),
)

#: How many read timeouts a call may take before it counts as waiting
#: without end.
_MARGIN = 3

#: How long to watch a read when the app sets no timeout, so that the test
#: fails by watching it wait rather than on the missing option.
_UNBOUNDED_WATCH_SECONDS = 2.0

#: Far more than the sender's and the peer's socket buffers hold together,
#: so a write to a peer that does not read has to block.
_UNREAD_BYTES = 64 * 1024 * 1024


def _watch_seconds() -> float:
    timeout = PublishingConnection(host="127.0.0.1:1").read_timeout
    return timeout * _MARGIN if timeout else _UNBOUNDED_WATCH_SECONDS


@pytest.fixture
def silent_broker() -> Iterator[str]:
    server = socket.create_server(("127.0.0.1", 0))
    accepted: list[socket.socket] = []

    def accept() -> None:
        peer, _ = server.accept()
        accepted.append(peer)

    acceptor = threading.Thread(target=accept, daemon=True)
    acceptor.start()
    host, port = server.getsockname()
    yield f"{host}:{port}"
    acceptor.join(timeout=1)
    for peer in accepted:
        peer.close()
    server.close()


@pytest.fixture
def publishing_transport(silent_broker: str) -> Iterator[TCPTransport]:
    connection = PublishingConnection(host=silent_broker)
    transport = connection.Transport(
        silent_broker,
        connection.connect_timeout,
        connection.ssl,
        connection.read_timeout,
        connection.write_timeout,
        socket_settings=connection.socket_settings,
    )
    transport.connect()
    yield transport
    transport.close()


def _run_bounded(
    call: Callable[[], object], seconds: float
) -> tuple[bool, BaseException | None, float]:
    """Run ``call`` in a thread for at most ``seconds``.

    Returns whether it was still running, what it raised, and how long it
    took, so a call that would wait without end fails the test instead of
    hanging it.
    """
    raised: list[BaseException] = []

    def run() -> None:
        try:
            call()
        except BaseException as exc:
            raised.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    started = time.monotonic()
    worker.start()
    worker.join(seconds)
    return worker.is_alive(), (raised or [None])[0], time.monotonic() - started


@linux_only
def test_a_publishing_connection_bounds_every_read():
    assert (PublishingConnection(host="127.0.0.1:1").read_timeout or 0) > 0


@linux_only
def test_waiting_for_a_reply_that_never_comes_ends_in_a_connection_error(
    publishing_transport,
):
    still_waiting, raised, took = _run_bounded(
        publishing_transport.read_frame, _watch_seconds()
    )

    assert not still_waiting, f"still waiting after {took:.1f} s"
    assert isinstance(raised, Transport.connection_errors), raised


@linux_only
def test_writing_to_a_broker_that_stopped_reading_ends_in_a_connection_error(
    publishing_transport,
):
    """py-amqp's default ``TCP_USER_TIMEOUT`` ends it, which is why the app
    sets no ``write_timeout``; this fails if the app's socket settings ever
    take that away."""
    still_writing, raised, took = _run_bounded(
        lambda: publishing_transport.write(b"\0" * _UNREAD_BYTES),
        _watch_seconds(),
    )

    assert not still_writing, f"still writing after {took:.1f} s"
    assert isinstance(raised, Transport.connection_errors), raised
