# Cart agent token

How the storefront's shopping assistant acts on a **signed-in** shopper's
cart. Source: `cart/agent_token.py`, `cart/services.py` (`CartService`),
`cart/views/cart.py` (`agent_token`), `core/api/tokens.py`
(`is_auth_token_live`). The code wins over this page.

## Why it exists

The assistant is served by the agent gateway, which calls this API
anonymously. A guest cart is reached with `X-Cart-Id`, but an anonymous
caller naming a signed-in shopper's cart never reaches it: the lookup
only matches guest carts, so the call quietly lands in a **new** guest
cart and reports success. The token gives the gateway a credential for
exactly one signed-in cart, and nothing else.

## Contract

`POST /api/v1/cart/agent-token` (Knox login only, no body):

```json
{ "token": "…", "expiresIn": 1800 }
```

- The token is bound to the caller's **own** cart (created when the
  shopper has none) and to the Knox login that minted it. A forced or
  session-only login has no Knox token and gets 403.
- `expiresIn` is `CART_AGENT_TOKEN_TTL_SECONDS` (default 1800): one pass
  per conversation. The storefront mints a new token when a call is
  refused with 401.
- Throttled by the `cart_agent_token` scope (fails closed).

The gateway then sends `X-Cart-Token: <token>` on its calls.

## What the token may do

| Route | Allowed |
|---|---|
| `GET /cart`, `GET /cart/coupons` | yes |
| `GET/POST /cart/item`, `GET/PUT/PATCH/DELETE /cart/item/<id>` | yes |
| `POST/DELETE /cart/coupon` | yes |
| `PUT/PATCH/DELETE /cart`, `reserve-stock`, `release-reservations`, `create-payment-intent`, orders, everything else | **403** `cart_token_not_permitted` |

The allowlist is `CartViewSet._CART_TOKEN_ACTIONS` plus every action of
`CartItemViewSet`; both pass `accepts_cart_token` to `CartService`, whose
default is `False`. It is deliberately **not** an authentication class:
the request stays anonymous, so no other endpoint (profile, orders)
treats the header as a login.

## Refusals

An unknown, expired or revoked token answers **401**. It is never
replaced by a guest cart. The body is the plain DRF error, `{"detail": ...}`:
the codes named on this page (`cart_token_invalid`, `cart_token_not_permitted`)
are the exceptions' internal codes and are not serialised, so a client tells
the cases apart by status. On a request that carries the token and no other
credential, a 401 can only mean the token was refused; the caller mints a new
one and retries. Checked on every use:

- the token is still in the cache (TTL),
- the minting Knox token still exists, has not expired and is within
  `KNOX_ABSOLUTE_MAX_AGE` (logging out revokes the token),
- the cart still exists, still belongs to the minting user, and that user
  is active.

## Rules that hold it together

- **Stored hashed.** The tenant cache (`tenant.cache.make_tenant_key`
  prefixes the schema, so a token minted on one store is a miss on
  another) holds `cart:agent-token:<sha256(token)>` mapping to the cart
  id, user id and Knox digest. The raw token is never stored or logged;
  the throttle keys on its hash.
- **Token wins over `X-Cart-Id`**, which is ignored when both are sent.
- **Pricing binds to the cart's owner**, not `request.user`
  (`CartService.owner`): wholesale buyers see wholesale prices and
  coupons are judged against the owner, though the request is anonymous.
- **Attribution is optional on the token path.** `POST /cart/item`
  takes the same `attribution` object as on the storefront and records
  the cart's first touch from it (`docs/order-system.md` section 3.4).
  The gateway sends none, so a cart it fills first records nothing.
- A new route that should accept the token must be added to the
  allowlist on purpose; reading `X-Cart-Token` anywhere but
  `CartService` defeats the design.
