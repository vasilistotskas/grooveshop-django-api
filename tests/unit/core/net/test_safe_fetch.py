"""``core.net.safe_fetch``: what it refuses and where it connects.

A local HTTP server stands in for the remote one. ``create_connection`` is
replaced so that a connection to the address safe_fetch CHECKED (a public
one, which pytest-socket would block) is carried to the local server, and
the address it was asked for is recorded: that is what proves the
connection goes to the checked IP and not to the name.
"""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import pytest
import urllib3

from core.net import safe_fetch as module
from core.net.safe_fetch import (
    FetchError,
    FetchStatusError,
    FetchTemporaryError,
    FetchTimeoutError,
    ResponseTooLargeError,
    TooManyRedirectsError,
    UnsafeUrlError,
    is_public_address,
    safe_fetch,
)

PUBLIC_IP = "93.184.216.34"
OTHER_PUBLIC_IP = "1.1.1.1"
MAX_BYTES = 1_000
HOST = "img.example.com"


class _Handler(BaseHTTPRequestHandler):
    seen: list[dict[str, str]] = []

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:
        type(self).seen.append({"path": self.path, **dict(self.headers)})
        redirects = {
            "/redirect-ok": (302, "/ok"),
            "/redirect-private": (302, "http://10.0.0.5/secret"),
            "/redirect-metadata": (301, "http://169.254.169.254/x"),
            "/redirect-userinfo": (302, "http://u:p@example.org/"),
            "/redirect-ftp": (302, "ftp://example.org/x"),
            "/loop": (302, "/loop"),
        }
        if self.path in redirects:
            status, location = redirects[self.path]
            self._send(status, b"", {"Location": location})
        elif self.path == "/ok":
            self._send(200, b"image-bytes", {"Content-Type": "image/png"})
        elif self.path == "/declared-big":
            self._send(200, b"x" * 5_000)
        elif self.path == "/streamed-big":
            self.send_response(200)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"x" * 5_000)
        elif self.path == "/missing":
            self._send(404, b"nope")
        elif self.path == "/down":
            self._send(503, b"later")
        elif self.path == "/limited":
            self._send(429, b"slow down")
        else:
            self._send(200, b"other")

    def _send(self, status: int, body: bytes, headers=None) -> None:
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server():
    _Handler.seen = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _answers(*addresses: str):
    return [
        (
            socket.AF_INET6 if ":" in address else socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            (address, 80),
        )
        for address in addresses
    ]


@pytest.fixture
def dns():
    """``dns.answer = [...]`` sets what every lookup returns."""
    with patch("core.net.safe_fetch.socket.getaddrinfo") as lookup:
        lookup.side_effect = lambda *args, **kwargs: _answers(*lookup.answer)
        lookup.answer = [PUBLIC_IP]
        yield lookup


def _dial(server) -> socket.socket:
    # Not ``socket.create_connection``: it resolves through the patched
    # ``getaddrinfo``.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(server.server_address)
    return sock


@pytest.fixture
def connections(server):
    """Record each address connected to and carry it to the local server."""
    requested: list[tuple[str, int]] = []

    def connect(address, *args, **kwargs):
        requested.append(address)
        return _dial(server)

    with patch("urllib3.util.connection.create_connection", connect):
        yield requested


@pytest.fixture
def no_connection():
    with patch("urllib3.util.connection.create_connection") as connect:
        yield connect
        connect.assert_not_called()


BLOCKED_ADDRESSES = [
    "127.0.0.1",
    "127.1.2.3",
    "0.0.0.0",
    "10.0.0.1",
    "172.16.0.1",
    "192.168.1.1",
    "169.254.169.254",
    "100.64.0.1",
    "224.0.0.1",
    "239.255.255.250",
    "255.255.255.255",
    "::1",
    "::",
    "fc00::1",
    "fd12:3456::1",
    "fe80::1",
    "ff02::1",
    "::ffff:10.0.0.1",
    "::ffff:127.0.0.1",
    "::ffff:169.254.169.254",
]


@pytest.mark.parametrize("address", BLOCKED_ADDRESSES)
def test_a_host_that_resolves_to_a_blocked_range_is_refused(
    address, dns, no_connection
):
    dns.answer = [address]

    with pytest.raises(UnsafeUrlError):
        safe_fetch(f"http://{HOST}/a.png", max_bytes=MAX_BYTES)


@pytest.mark.parametrize("address", BLOCKED_ADDRESSES)
def test_a_blocked_address_is_not_public(address):
    assert is_public_address(address) is False


@pytest.mark.parametrize("address", [PUBLIC_IP, "2606:4700:4700::1111"])
def test_a_public_address_is_public(address):
    assert is_public_address(address) is True


def test_one_blocked_answer_among_public_ones_refuses_the_host(
    dns, no_connection
):
    dns.answer = [PUBLIC_IP, "10.0.0.1"]

    with pytest.raises(UnsafeUrlError):
        safe_fetch(f"http://{HOST}/a.png", max_bytes=MAX_BYTES)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/a.png",
        "http://[::1]/a.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:10.0.0.1]/a.png",
        "http://2130706433/a.png",
        "http://0x7f000001/a.png",
        "http://localhost/a.png",
    ],
)
def test_a_literal_blocked_address_is_refused(url, no_connection):
    with pytest.raises(FetchError):
        safe_fetch(url, max_bytes=MAX_BYTES)


@pytest.mark.parametrize(
    "url",
    [
        f"http://user:pass@{HOST}/a.png",
        f"http://user@{HOST}/a.png",
        f"https://:secret@{HOST}/a.png",
        f"http://{HOST}:8080/a.png",
        f"http://{HOST}:22/a.png",
        f"https://{HOST}:8443/a.png",
        f"ftp://{HOST}/a.png",
        "file:///etc/passwd",
        f"gopher://{HOST}/",
        "javascript:alert(1)",
        f"//{HOST}/a.png",
        "/a.png",
        "http:///a.png",
        f"http://{HOST}:notaport/a.png",
    ],
)
def test_a_url_outside_the_rules_is_refused(url, dns, no_connection):
    with pytest.raises(UnsafeUrlError):
        safe_fetch(url, max_bytes=MAX_BYTES)
    dns.assert_not_called()


def test_it_connects_to_the_checked_address_with_the_hostname_in_host(
    server, dns, connections
):
    result = safe_fetch(f"http://{HOST}/ok", max_bytes=MAX_BYTES)

    assert result.content == b"image-bytes"
    assert result.content_type == "image/png"
    assert connections == [(PUBLIC_IP, 80)]
    assert _Handler.seen[0]["Host"] == HOST


def test_a_dns_answer_that_changes_after_the_check_is_never_used(
    server, dns, connections
):
    # First lookup: public, so the check passes. Any later lookup would
    # answer with the metadata service. Resolving only once, and
    # connecting to that address, leaves the rebinding no window.
    answers = [[PUBLIC_IP], ["169.254.169.254"]]
    dns.side_effect = lambda *a, **k: _answers(*answers.pop(0))

    result = safe_fetch(f"http://{HOST}/ok", max_bytes=MAX_BYTES)

    assert result.content == b"image-bytes"
    assert connections == [(PUBLIC_IP, 80)]
    assert dns.call_count == 1


def test_a_second_address_is_tried_when_the_first_refuses(server, dns):
    dns.answer = [PUBLIC_IP, OTHER_PUBLIC_IP]
    attempts: list[tuple[str, int]] = []

    def connect(address, *args, **kwargs):
        attempts.append(address)
        if address[0] == PUBLIC_IP:
            raise ConnectionRefusedError
        return _dial(server)

    with patch("urllib3.util.connection.create_connection", connect):
        result = safe_fetch(f"http://{HOST}/ok", max_bytes=MAX_BYTES)

    assert result.content == b"image-bytes"
    assert attempts == [(PUBLIC_IP, 80), (OTHER_PUBLIC_IP, 80)]


def test_a_fragment_is_not_sent(server, dns, connections):
    safe_fetch(f"http://{HOST}/ok#section", max_bytes=MAX_BYTES)

    assert _Handler.seen[0]["path"] == "/ok"


def test_a_redirect_is_followed_and_rechecked(server, dns, connections):
    result = safe_fetch(f"http://{HOST}/redirect-ok", max_bytes=MAX_BYTES)

    assert result.content == b"image-bytes"
    assert result.url == f"http://{HOST}/ok"
    assert dns.call_count == 2


@pytest.mark.parametrize(
    "path",
    [
        "/redirect-private",
        "/redirect-metadata",
        "/redirect-userinfo",
        "/redirect-ftp",
    ],
)
def test_a_redirect_to_a_refused_target_is_refused(
    path, server, dns, connections
):
    # The first hop is public and answers; the Location it names is not.
    def lookup(host, *args, **kwargs):
        return _answers(PUBLIC_IP if host == HOST else host)

    dns.side_effect = lookup

    with pytest.raises(UnsafeUrlError):
        safe_fetch(f"http://{HOST}{path}", max_bytes=MAX_BYTES)

    assert connections == [(PUBLIC_IP, 80)]


def test_more_than_three_redirects_are_refused(server, dns, connections):
    with pytest.raises(TooManyRedirectsError):
        safe_fetch(f"http://{HOST}/loop", max_bytes=MAX_BYTES)

    assert len(connections) == module.MAX_REDIRECTS + 1


def test_a_body_that_declares_more_than_the_cap_is_refused(
    server, dns, connections
):
    with pytest.raises(ResponseTooLargeError):
        safe_fetch(f"http://{HOST}/declared-big", max_bytes=MAX_BYTES)


def test_a_body_streamed_past_the_cap_is_refused(server, dns, connections):
    with pytest.raises(ResponseTooLargeError):
        safe_fetch(f"http://{HOST}/streamed-big", max_bytes=MAX_BYTES)


def test_a_body_exactly_at_the_cap_is_accepted(server, dns, connections):
    size = len(b"image-bytes")

    result = safe_fetch(f"http://{HOST}/ok", max_bytes=size)

    assert result.content == b"image-bytes"


def test_a_4xx_is_permanent(server, dns, connections):
    with pytest.raises(FetchStatusError) as caught:
        safe_fetch(f"http://{HOST}/missing", max_bytes=MAX_BYTES)

    assert caught.value.status == 404
    assert not isinstance(caught.value, ConnectionError)


@pytest.mark.parametrize("path", ["/down", "/limited"])
def test_a_5xx_and_a_429_are_retryable(path, server, dns, connections):
    with pytest.raises(FetchTemporaryError) as caught:
        safe_fetch(f"http://{HOST}{path}", max_bytes=MAX_BYTES)

    assert isinstance(caught.value, ConnectionError)


def test_a_refused_connection_is_retryable(dns):
    def refuse(address, *args, **kwargs):
        raise ConnectionRefusedError

    with (
        patch("urllib3.util.connection.create_connection", refuse),
        pytest.raises(FetchTemporaryError),
    ):
        safe_fetch(f"http://{HOST}/ok", max_bytes=MAX_BYTES)


def test_a_connect_timeout_is_a_timeout(dns):
    def hang(address, *args, **kwargs):
        raise TimeoutError

    with (
        patch("urllib3.util.connection.create_connection", hang),
        pytest.raises(FetchTimeoutError) as caught,
    ):
        safe_fetch(f"http://{HOST}/ok", max_bytes=MAX_BYTES)

    assert isinstance(caught.value, TimeoutError)


def test_an_unresolvable_host_is_a_permanent_fetch_error(no_connection):
    with (
        patch(
            "core.net.safe_fetch.socket.getaddrinfo",
            side_effect=socket.gaierror(socket.EAI_NONAME, "unknown"),
        ),
        pytest.raises(FetchError) as caught,
    ):
        safe_fetch("http://nope.example.com/a.png", max_bytes=MAX_BYTES)

    assert not isinstance(caught.value, ConnectionError)


def test_the_total_deadline_ends_the_fetch(server, dns, connections):
    with (
        patch.object(module, "TOTAL_TIMEOUT", 0),
        pytest.raises(FetchTimeoutError),
    ):
        safe_fetch(f"http://{HOST}/ok", max_bytes=MAX_BYTES)

    assert connections == []


def test_the_timeouts_are_five_fifteen_thirty():
    assert module.CONNECT_TIMEOUT == 5
    assert module.READ_TIMEOUT == 15
    assert module.TOTAL_TIMEOUT == 30


def test_https_connects_to_the_ip_and_verifies_the_hostname(dns):
    pool = MagicMock()
    pool.__enter__.return_value = pool
    pool.urlopen.side_effect = urllib3.exceptions.ProtocolError("stop here")

    with (
        patch("core.net.safe_fetch.urllib3.HTTPSConnectionPool") as pool_class,
        pytest.raises(FetchTemporaryError),
    ):
        pool_class.return_value = pool
        safe_fetch(f"https://{HOST}/a.png", max_bytes=MAX_BYTES)

    args, kwargs = pool_class.call_args
    assert args == (PUBLIC_IP, 443)
    assert kwargs["server_hostname"] == HOST
    assert kwargs["assert_hostname"] == HOST
    assert kwargs["cert_reqs"] == "CERT_REQUIRED"
    assert kwargs["timeout"].connect_timeout <= module.CONNECT_TIMEOUT
    assert kwargs["timeout"].read_timeout <= module.READ_TIMEOUT
    request = pool.urlopen.call_args.kwargs
    assert request["headers"] == {"Host": HOST}
    assert request["assert_same_host"] is False
    assert request["redirect"] is False


def test_a_certificate_for_another_host_is_permanent(dns):
    pool = MagicMock()
    pool.__enter__.return_value = pool
    pool.urlopen.side_effect = urllib3.exceptions.SSLError("hostname mismatch")

    with (
        patch("core.net.safe_fetch.urllib3.HTTPSConnectionPool") as pool_class,
        pytest.raises(FetchError) as caught,
    ):
        pool_class.return_value = pool
        safe_fetch(f"https://{HOST}/a.png", max_bytes=MAX_BYTES)

    assert not isinstance(caught.value, ConnectionError)
