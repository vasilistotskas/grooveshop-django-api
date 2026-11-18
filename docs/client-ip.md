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
that mixin, and every pair of cart, order and payment budgets uses it: the
anonymous half (`GatewayAnonThrottle`) and the signed-in half
(`CartMutationThrottle`, `OrderCreateThrottle`, `PaymentAttemptThrottle`,
whose `UserRateThrottle` keys a caller who is not signed in on
`get_ident`), the coupon budget (`CouponApplyThrottle`) and the gift-card
guessing budget (`GiftCardCodeThrottle`).

**The address is the key unless the secret verifies and the request names
something that exists.** What counts is settled once per request, by
`cart.agent_token.existing_gateway_subject`: a cart token that was minted
and has not expired (keyed by its hash); else a guest cart, by the uuid the
cart lookup parses (`core.cart_id.cart_uuid_from_request`, so upper case, no
hyphens, braces or `urn:uuid:` are one cart); else, only for the payment
throttles (`gateway_names_order`), an order (keyed by its hash). A cart
route's `?uuid=` names no order and is ignored. An id that is no uuid, names
no cart or order, or comes without the secret is keyed on the address, as
before the gateway rule. The cart writes create a cart when the one they
name does not resolve (a cart-item create, a coupon apply), so an id that
earned a budget just by being well formed would let a client that makes ids
up escape the per-address burst limit on cart creation and the tight coupon
guessing limit. For the coupon budget, guessing stays bounded for the same
reason: a cart is minted by a cart write, which names no cart yet and is
budgeted per address (`cart_mutation`).

**Two readings of an order uuid, one rule.** A cache key could accept any
spelling, but guest authorization (`Order.opens_for_guest`) accepts only the
canonical string (lower case, hyphenated), and the payment budget must charge
exactly what authorization can accept, or refused spellings would use up the
budget of a corrected retry. Both go through `core.canonical_uuid`: a
non-canonical spelling is keyed on the address, never on the order. (A
cart's id is different: the cart lookup itself accepts every spelling, so
the throttle follows it.)

The gift-card guessing budget is keyed on the signed-in customer, else the
gateway's cart, else the address, and counts only codes new to the cart
(`docs/order/creation.md` §3.0). The BoxNow nearest-locker budget stays
per address: the endpoint calls BOX NOW live and the gateway's call names
no cart.
`edge_client_ip` itself never falls back to
`REMOTE_ADDR`. An address handed on as the visitor's own, such as the
`client_ip_address` of a Meta Conversions API event
(`src/meta_capi/signals.py`), is `edge_client_ip` or nothing: the peer is
a proxy, not the visitor. It is named for where the proof comes from: CodeQL treats
any function whose name says `trusted` or `secret` as a source of
secrets, so under its old name every log line carrying an address was a
clear-text-logging alert. Operator-side detail, including the fact that the secret
lives in two separate Cloudflare accounts, is in
`grooveshop-infrastructure/docs/edge-trust-boundary.md`.
