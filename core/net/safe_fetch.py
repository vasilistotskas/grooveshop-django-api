"""Fetch a URL a stranger chose without letting it reach our network.

A catalogue file names image URLs and a social provider names an avatar
URL. Fetched with a bare ``requests.get``, either could point the worker at
``http://169.254.169.254/`` (the cloud metadata service), at a service on the
cluster network, or at ``localhost``. :func:`safe_fetch` is the one way to
download such a URL:

- the scheme is ``http`` or ``https``, the port 80 or 443, and the URL
  carries no ``user:password@``;
- the host is resolved ONCE and every address must be a public one
  (:func:`is_public_address`); one private answer refuses the URL;
- the connection goes to that checked address, never to the name again, so
  DNS cannot answer differently between the check and the connect
  (rebinding). The ``Host`` header, the TLS SNI and the certificate check
  keep the hostname (urllib3's ``server_hostname`` and ``assert_hostname``,
  https://urllib3.readthedocs.io/en/stable/advanced-usage.html, "Custom SNI
  Hostname");
- at most :data:`MAX_REDIRECTS` redirects, each target checked like the
  first URL;
- :data:`CONNECT_TIMEOUT` to connect, :data:`READ_TIMEOUT` for each read,
  :data:`TOTAL_TIMEOUT` for the whole fetch, and a byte cap the caller gives,
  counted on the bytes as they stream in.

Failures are typed for the retry rules of ``core.retry``: a refused URL, a
4xx and an oversize body are permanent (:class:`FetchError`); a refused
connection, a timeout, a 5xx, 408 and 429 are
:class:`FetchTemporaryError` / :class:`FetchTimeoutError`, which are a
``ConnectionError`` / ``TimeoutError`` so ``TRANSIENT_ERRORS`` retries them.

See ``docs/safe-fetch.md``.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import SplitResult, urljoin, urlsplit

import urllib3
from urllib3.exceptions import (
    ConnectTimeoutError,
    HTTPError,
    NewConnectionError,
    ReadTimeoutError,
    SSLError,
)

from core.retry import is_permanent_http_status

ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_PORTS = frozenset({80, 443})
DEFAULT_PORTS = {"http": 80, "https": 443}
MAX_REDIRECTS = 3
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 15.0
TOTAL_TIMEOUT = 30.0
CHUNK_BYTES = 64 * 1024
REDIRECT_STATUSES = frozenset(
    {
        HTTPStatus.MOVED_PERMANENTLY,
        HTTPStatus.FOUND,
        HTTPStatus.SEE_OTHER,
        HTTPStatus.TEMPORARY_REDIRECT,
        HTTPStatus.PERMANENT_REDIRECT,
    }
)


class FetchError(Exception):
    """The URL cannot be fetched and asking again will not change that."""


class UnsafeUrlError(FetchError):
    """The URL, a redirect target or an address it resolves to is refused."""


class TooManyRedirectsError(FetchError):
    """The redirects did not end within :data:`MAX_REDIRECTS`."""


class ResponseTooLargeError(FetchError):
    """The body is larger than the caller's byte cap."""


class FetchStatusError(FetchError):
    """The server answered with a status that is not a success."""

    def __init__(self, status: int, url: str) -> None:
        super().__init__(f"{url} answered {status}")
        self.status = status


class FetchTemporaryError(FetchError, ConnectionError):
    """The server could not be reached or asked for another attempt."""


class FetchTimeoutError(FetchError, TimeoutError):
    """A connect, a read or the whole fetch ran out of time."""


@dataclass(frozen=True, slots=True)
class FetchedResponse:
    """What came back: the final URL, the body and its declared type."""

    url: str
    content: bytes
    content_type: str


def is_public_address(address: str) -> bool:
    """Whether ``address`` is a globally routable unicast address.

    ``is_global`` alone lets multicast through, and an IPv4-mapped IPv6
    address (``::ffff:10.0.0.1``) is judged by the IPv4 address it wraps.
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def safe_fetch(url: str, *, max_bytes: int) -> FetchedResponse:
    """GET ``url`` and return its body, within every rule of this module.

    ``max_bytes`` is the most body bytes accepted (after content decoding).
    Raises a :class:`FetchError` (see the module docstring for which are
    retried).
    """
    deadline = time.monotonic() + TOTAL_TIMEOUT
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        response, final_url = _request(current, deadline, max_bytes)
        if isinstance(response, str):
            current = urljoin(final_url, response)
            continue
        return response
    raise TooManyRedirectsError(
        f"More than {MAX_REDIRECTS} redirects from {url}"
    )


def _request(
    url: str, deadline: float, max_bytes: int
) -> tuple[FetchedResponse | str, str]:
    """One hop: the response, or the redirect target to follow."""
    parts = _validate_url(url)
    hostname = parts.hostname or ""
    port = parts.port or DEFAULT_PORTS[parts.scheme]
    addresses = _resolve(hostname, port)
    target = parts._replace(scheme="", netloc="", fragment="").geturl() or "/"
    last_error: FetchError | None = None
    for address in addresses:
        try:
            response = _get(
                parts,
                address,
                port=port,
                hostname=hostname,
                target=target,
                deadline=deadline,
                max_bytes=max_bytes,
            )
            return response, url
        except FetchTemporaryError as exc:
            # Only a failure to connect moves on to the next address.
            if not isinstance(exc.__cause__, NewConnectionError):
                raise
            last_error = exc
    assert last_error is not None
    raise last_error


def _validate_url(url: str) -> SplitResult:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafeUrlError(f"Not a valid URL: {url!r}") from exc
    if parts.scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"Scheme {parts.scheme!r} is not allowed")
    if not parts.hostname:
        raise UnsafeUrlError("The URL has no host")
    if parts.username is not None or parts.password is not None:
        raise UnsafeUrlError("The URL must not carry credentials")
    if (port or DEFAULT_PORTS[parts.scheme]) not in ALLOWED_PORTS:
        raise UnsafeUrlError(f"Port {port} is not allowed")
    return parts


def _resolve(hostname: str, port: int) -> list[str]:
    """Every address of ``hostname``, each one checked, in answer order."""
    try:
        infos = socket.getaddrinfo(
            hostname, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror as exc:
        if exc.errno == socket.EAI_AGAIN:
            raise FetchTemporaryError(
                f"Could not resolve {hostname}: {exc}"
            ) from exc
        raise FetchError(f"Could not resolve {hostname}: {exc}") from exc
    addresses: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if not is_public_address(address):
            raise UnsafeUrlError(f"{hostname} resolves to {address}")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise FetchError(f"{hostname} has no address")
    return addresses


def _get(
    parts: SplitResult,
    address: str,
    *,
    port: int,
    hostname: str,
    target: str,
    deadline: float,
    max_bytes: int,
) -> FetchedResponse | str:
    """GET ``target`` from ``address``; the body, or the redirect target."""
    timeout = urllib3.Timeout(
        connect=min(CONNECT_TIMEOUT, _remaining(deadline)),
        read=min(READ_TIMEOUT, _remaining(deadline)),
    )
    if parts.scheme == "https":
        pool = urllib3.HTTPSConnectionPool(
            address,
            port,
            timeout=timeout,
            retries=False,
            cert_reqs="CERT_REQUIRED",
            server_hostname=hostname,
            assert_hostname=hostname,
        )
    else:
        pool = urllib3.HTTPConnectionPool(
            address, port, timeout=timeout, retries=False
        )
    host_header = f"[{hostname}]" if ":" in hostname else hostname
    if parts.port is not None:
        host_header = f"{host_header}:{port}"
    with pool:
        try:
            response = pool.urlopen(
                "GET",
                target,
                headers={"Host": host_header},
                redirect=False,
                assert_same_host=False,
                preload_content=False,
                retries=False,
            )
        except HTTPError as exc:
            raise _classify(exc, hostname) from exc
        try:
            return _read(response, parts, deadline, max_bytes)
        except (ReadTimeoutError, HTTPError) as exc:
            raise _classify(exc, hostname) from exc
        finally:
            response.release_conn()
            response.close()


def _read(
    response: urllib3.BaseHTTPResponse,
    parts: SplitResult,
    deadline: float,
    max_bytes: int,
) -> FetchedResponse | str:
    url = parts.geturl()
    status = response.status
    if status in REDIRECT_STATUSES:
        location = response.headers.get("Location")
        if not location:
            raise FetchStatusError(status, url)
        return location
    if not HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES:
        if is_permanent_http_status(status):
            raise FetchStatusError(status, url)
        raise FetchTemporaryError(f"{url} answered {status}")
    declared = response.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise ResponseTooLargeError(f"{url} is {declared} bytes")
    body = bytearray()
    for chunk in response.stream(CHUNK_BYTES, decode_content=True):
        body += chunk
        if len(body) > max_bytes:
            raise ResponseTooLargeError(f"{url} is over {max_bytes} bytes")
        if time.monotonic() > deadline:
            raise FetchTimeoutError(f"{url} took over {TOTAL_TIMEOUT}s")
    return FetchedResponse(
        url=url,
        content=bytes(body),
        content_type=response.headers.get("Content-Type", ""),
    )


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise FetchTimeoutError(f"The fetch took over {TOTAL_TIMEOUT}s")
    return remaining


def _classify(exc: HTTPError, hostname: str) -> FetchError:
    """The typed error for a urllib3 failure.

    A certificate that does not match the hostname is permanent; the
    other failures (refused, reset, timed out) are worth another try.
    """
    if isinstance(exc, SSLError):
        return FetchError(f"{hostname} failed TLS verification: {exc}")
    # First: urllib3's NewConnectionError is a ConnectTimeoutError.
    if isinstance(exc, NewConnectionError):
        return FetchTemporaryError(f"Could not connect to {hostname}")
    if isinstance(exc, ConnectTimeoutError | ReadTimeoutError):
        return FetchTimeoutError(f"{hostname} timed out")
    return FetchTemporaryError(f"{hostname}: {exc}")
