# Fetching a URL a stranger chose (`core/net/safe_fetch.py`)

A catalogue file names image URLs and a social provider names an avatar URL.
Fetched with a bare `requests.get`, either could point a worker at
`http://169.254.169.254/` (the cloud metadata service), at a service on the
cluster network or at `localhost` (SSRF). `core.net.safe_fetch.safe_fetch(url,
max_bytes=...)` is the one way to download such a URL. Its callers today are
the social-avatar task (`user/tasks.py`, URL from `user/signals.py`); the
catalogue import's image phase will be the second.

## The rules

| Rule | How |
|---|---|
| `http` or `https`, port 80 or 443 only, no `user:password@` | `ALLOWED_SCHEMES`, `ALLOWED_PORTS`, checked on the first URL and on every redirect target |
| Every address the host resolves to is public | `is_public_address`: `ipaddress.ip_address(...).is_global` and not multicast (`is_global` alone lets `224.0.0.0/4` through); an IPv4-mapped IPv6 address (`::ffff:10.0.0.1`) is judged by the IPv4 address it wraps. One refused answer refuses the host |
| No DNS rebinding | The host is resolved ONCE per hop and the connection goes to that checked address, never to the name again. A second answer is never asked for |
| Hostname kept for TLS and `Host` | The pool is built on the IP with `server_hostname` (SNI) and `assert_hostname` set to the hostname and `cert_reqs="CERT_REQUIRED"`; the request carries `Host: <hostname>` and `assert_same_host=False` (urllib3 advanced usage, "Custom SNI Hostname"). A certificate that does not match is a permanent error |
| At most 3 redirects, each re-checked | `MAX_REDIRECTS`; the `Location` is joined to the current URL and goes through the same checks, so a redirect to `10.0.0.5` or `169.254.169.254` is refused before any connection |
| 5 s connect, 15 s per read, 30 s in all | `CONNECT_TIMEOUT`, `READ_TIMEOUT`, `TOTAL_TIMEOUT`: one deadline for the whole fetch, redirects included |
| A byte cap | `max_bytes` is the caller's. A `Content-Length` over it is refused before a byte is read, and the body is counted as it streams (after content decoding, so a compressed body cannot slip past) |

A fragment is never sent. When a host has several public addresses a refused
connection moves on to the next one.

## Errors and retries

The failures are typed for `core/retry.py`:

- Permanent, never retried (`FetchError`): `UnsafeUrlError` (URL, redirect
  target or address refused), `TooManyRedirectsError`, `ResponseTooLargeError`,
  `FetchStatusError` (a 4xx other than 408 and 429, decided by
  `core.retry.is_permanent_http_status`), a certificate mismatch and an
  unresolvable host.
- Retryable: `FetchTemporaryError` is a `ConnectionError` (refused or reset
  connection, a 5xx, 408, 429, a temporary DNS failure) and `FetchTimeoutError`
  is a `TimeoutError`, so a task with `autoretry_for=TRANSIENT_ERRORS` retries
  exactly these.

## Using it

```python
from core.net.safe_fetch import FetchError, safe_fetch
from core.uploads import image_limits

fetched = safe_fetch(url, max_bytes=image_limits().largest_file_bytes)
fetched.content, fetched.content_type, fetched.url
```

Images pass the cap from `image_limits()` (`docs/image-uploads.md`) and then
`prepare_servable_image`: the cap bounds what is downloaded, the upload rule
decides what is kept.

## Tests

`tests/unit/core/net/test_safe_fetch.py` runs a local HTTP server and replaces
`urllib3.util.connection.create_connection` with a function that records the
address it was asked for and dials the local server, because pytest-socket
(`docs/testing.md`) blocks any other connect. It covers every blocked range
(loopback, private, link-local including `169.254.169.254`, CGNAT, multicast,
IPv6 ULA and link-local, IPv4-mapped IPv6), literal and decimal/hex IPs, a
redirect to a private address, DNS that changes between the check and the
connect, userinfo, other ports and schemes, oversize bodies (declared and
streamed) and the connection going to the checked IP with the hostname in
`Host`. The https path is asserted on the pool's arguments (`server_hostname`,
`assert_hostname`, `cert_reqs`); no TLS handshake runs in the suite.

## Not covered

An egress NetworkPolicy for the worker would be defence in depth; it is not
part of this module.
