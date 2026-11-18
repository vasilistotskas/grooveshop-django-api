# Client IP and throttle identity

How the API identifies the caller behind a request, for throttles, audit
rows and anything else keyed on an address. Source: `src/core/client_ip.py`
(`edge_client_ip`, `client_ip_or_peer`). The code wins over this page.

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
it. Use `edge_client_ip(request)`, which believes Cloudflare's
`CF-Connecting-IP`/`X-Real-IP` **only** when the request carries the
`X-Origin-Verify` secret our Cloudflare Transform Rules stamp, else
returns `None`; everything that keys on a caller uses
`client_ip_or_peer(request)` (the proven visitor, else the rightmost
`X-Forwarded-For` hop Traefik appended, else `REMOTE_ADDR`). That includes
every DRF throttle: they are all defined in `src/core/api/throttling.py`,
whose `ResilientThrottleMixin.get_ident` is `client_ip_or_peer`, the
`anon`/`user` defaults too (`AnonIpRateThrottle`, `UserBudgetThrottle`).
DRF's own classes take the rightmost hop alone, the shared peer, and no
module imports them (`tests/unit/core/test_throttle_outage.py`). Some
budgets are not keyed on the address alone. A request carrying the agent
gateway's secret is identified by the cart or order it names
(`GatewayIdentMixin.get_ident`, `_gateway_ident`), because every buyer of
an AI platform arrives from the platform's address. The rule lives once, in
that mixin, and every pair of cart, order and payment budgets uses it:
the anonymous half (`GatewayAnonThrottle`) and the signed-in half
(`CartMutationThrottle`, `OrderCreateThrottle`, `PaymentAttemptThrottle`,
whose `UserRateThrottle` keys a caller who is not signed in on
`get_ident`). Without the secret the address is the key, so a forged
`X-Cart-Id` buys no budget. And the gift-card guessing budget
(`GiftCardCodeThrottle`) is keyed on the signed-in customer, else the
gateway's cart, else the address, and counts only codes new to the cart
(`docs/order/creation.md` §3.0). `edge_client_ip` itself never falls back to
`REMOTE_ADDR`. An address handed on as the visitor's own, such as the
`client_ip_address` of a Meta Conversions API event
(`src/meta_capi/signals.py`), is `edge_client_ip` or nothing: the peer is
a proxy, not the visitor. It is named for where the proof comes from: CodeQL treats
any function whose name says `trusted` or `secret` as a source of
secrets, so under its old name every log line carrying an address was a
clear-text-logging alert. Operator-side detail, including the fact that the secret
lives in two separate Cloudflare accounts, is in
`grooveshop-infrastructure/docs/edge-trust-boundary.md`.
