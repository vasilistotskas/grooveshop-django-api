# Client IP and throttle identity

How the API identifies the caller behind a request, for throttles, audit
rows and anything else keyed on an address. Source: `core/client_ip.py`
(`trusted_client_ip`, `client_ip_or_peer`). The code wins over this page.

Never read
`REMOTE_ADDR`, `X-Forwarded-For` or `X-Real-IP` directly to identify a
caller. What Traefik can vouch for is its TCP peer — a Cloudflare edge
node for proxied traffic (and a `10.42.x.x` SNAT gateway before the
Service moved to `externalTrafficPolicy: Local`; a public request was
recorded as `10.42.1.0` on 2026-09-16). Keying anonymous throttles on it
makes a budget shared by everyone behind that address
(`order_create_anon` at 10/minute let one client lock out every guest
checkout). `X-Real-IP` on the SSR path is the caller's own
`CF-Connecting-IP`, relayed by Nuxt, so a caller hitting a node IP chooses
it. Use `trusted_client_ip(request)`, which believes Cloudflare's
`CF-Connecting-IP`/`X-Real-IP` **only** when the request carries the
`X-Origin-Verify` secret our Cloudflare Transform Rules stamp, else
returns `None`; DRF throttles then fall back to `get_ident()`, and
everything else uses `client_ip_or_peer(request)` (the proven visitor,
else the rightmost `X-Forwarded-For` hop Traefik appended, else
`REMOTE_ADDR`). `trusted_client_ip` itself never falls back to
`REMOTE_ADDR`. Operator-side detail, including the fact that the secret
lives in two separate Cloudflare accounts, is in the infrastructure
repo's `docs/edge-trust-boundary.md`.
