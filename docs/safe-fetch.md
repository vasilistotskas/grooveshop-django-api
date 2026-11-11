# Fetching a URL a stranger chose (`src/core/net/safe_fetch.py`)

A catalogue file names image URLs and a social provider names an avatar URL.
Fetched with a bare `requests.get`, either could point a worker at
`http://169.254.169.254/` (the cloud metadata service), at a service on the
cluster network or at `localhost` (SSRF). `core.net.safe_fetch.safe_fetch(url,
max_bytes=...)` is the one way to download such a URL. Its callers today are
the social-avatar task (`src/user/tasks.py`, URL from `src/user/signals.py`, the bytes
then through `prepare_servable_image`, so a file that is not a servable raster
is dropped and GPS is stripped); the
catalogue import's image phase will be the second.

## The rules

| Rule | How |
|---|---|
| `http` or `https`, port 80 or 443 only, no `user:password@` | `ALLOWED_SCHEMES`, `ALLOWED_PORTS`, checked on the first URL and on every redirect target |
| Every address the host resolves to is public | `is_public_address`: `ipaddress.ip_address(...).is_global` and not multicast (`is_global` alone lets `224.0.0.0/4` through); an IPv4-mapped IPv6 address (`::ffff:10.0.0.1`) and the NAT64 well-known prefix (`64:ff9b::/96`, RFC 6052: the IPv4 address is the last 32 bits) are judged by the IPv4 address they carry; the IPv4-compatible `::/96` (RFC 4291, deprecated) and IPv4-translated `::ffff:0:0:0/96` (RFC 2765, obsoleted by RFC 7915) are refused, because `ipaddress` calls them global; 6to4, Teredo and the local-use NAT64 `64:ff9b:1::/48` are already not `is_global`. One refused answer refuses the host |
| No DNS rebinding | The host is resolved ONCE per hop and the connection goes to that checked address, never to the name again. A second answer is never asked for |
| Hostname kept for TLS and `Host` | The connection (`urllib3.connection.HTTPSConnection`, used directly: one request per connection needs no pool) is built on the IP with `server_hostname` set to the hostname (SNI) and `cert_reqs="CERT_REQUIRED"`, and the request carries `Host: <hostname>` (urllib3 advanced usage, "Custom SNI Hostname"). `assert_hostname` is deliberately not set: it makes urllib3 switch `check_hostname` off and match the name itself, raising its own `CertificateError`, a `ValueError`. With `server_hostname` alone `ssl` checks the certificate against the SNI name and raises `SSLCertVerificationError`: one verification path, one exception. The name is converted once to ASCII (IDNA, `bücher.de` becomes `xn--bcher-kva.de`) and that one name is resolved, sent as SNI, checked against the certificate and written in `Host`. A certificate that does not match is a permanent error |
| At most 3 redirects, each re-checked | `MAX_REDIRECTS`; the `Location` is joined to the current URL and goes through the same checks, so a redirect to `10.0.0.5` or `169.254.169.254` is refused before any connection |
| 5 s connect, 15 s per read, 30 s in all | `CONNECT_TIMEOUT`, `READ_TIMEOUT`, `TOTAL_TIMEOUT`: one deadline for the whole fetch, redirects and the DNS lookups included (`getaddrinfo` has no timeout and runs before the watchdog can arm, so it runs on a thread that is waited for no longer than the time left; a slow answer is a `FetchTimeoutError`). A socket timeout limits one `recv`, so a host sending a byte every 14 s would never trip it, in the headers or the body: `_Watchdog` (a `threading.Timer`) shuts the socket down at the deadline, which makes the blocked read return at once, and a body that ends because of it is a `FetchTimeoutError`, never a short success |
| A byte cap | `max_bytes` is the caller's. A `Content-Length` over it is refused before a byte is read, and the body is counted as it streams (after content decoding, so a compressed body cannot slip past) |

A fragment is never sent, a query with no path is sent after a `/` (`GET /?img=1`), and a request target is percent-encoded (`TARGET_SAFE`
keeps the reserved characters and existing escapes), so a Greek file name is
fetched, not refused. A host name with a control character (a NUL, a space) or
one that cannot be converted to IDNA (an empty or over-63-character label) is an
`UnsafeUrlError`: `safe_fetch` raises only `FetchError` subclasses. When a host
has several public addresses a refused connection moves on to the next one.

## Errors and retries

The failures are typed for `src/core/retry.py`:

- Permanent, never retried (`FetchError`): `UnsafeUrlError` (URL, redirect
  target or address refused), `TooManyRedirectsError`, `ResponseTooLargeError`,
  `FetchStatusError` (a 4xx other than 408 and 429, decided by
  `core.retry.is_permanent_http_status`), a failed certificate check
  (`ssl.SSLCertVerificationError` anywhere in the cause chain, and nothing
  else: a handshake that broke off or a protocol error is retried) and an
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
IPv6 ULA and link-local, IPv4-mapped, NAT64, IPv4-compatible and -translated, 6to4, Teredo), literal and decimal/hex IPs, a
redirect to a private address, DNS that changes between the check and the
connect, userinfo, other ports and schemes, oversize bodies (declared and
streamed) and the connection going to the checked IP with the hostname in
`Host`. Local servers that trickle the body or the headers one byte at a time
prove the total deadline (and that a cut-off body is not returned), and a slow
resolver is cut off at it. TLS is real: a module-scoped CA and a server
certificate (made with `cryptography`, trusted through `SSL_CERT_FILE`) for
`good.example` and the IDNA forms of a Latin-1 and a Greek host back an https
server, so the suite sees a matching certificate accepted, a certificate for
another host raising `ssl.SSLCertVerificationError` (surfacing as a permanent
`FetchError`), and IDN hosts verified and sent as ASCII over https and http.
The other TLS failures are classified with mocked connections.

## Not covered

An egress NetworkPolicy for the worker would be defence in depth; it is not
part of this module.
