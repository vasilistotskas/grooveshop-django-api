# allauth headless OpenAPI document

`allauth-schema.yml` (repository root) is the OpenAPI document of the
`/_allauth/app/v1/*` API that the storefront proxies for sign-in, sign-up,
two-factor, passkeys, email, password and connected accounts. The storefront
generates its all-auth parsers from it next to `schema.yml`, so it writes none
of them by hand.

## Where it comes from

allauth ships a hand-maintained specification and adapts it to the project at
call time (`allauth.headless.spec.internal.schema.get_schema`): it keeps the
paths of the configured `HEADLESS_CLIENTS`, pins the single client, and takes
the user object and the signup fields from our adapter, settings and signup
form. `manage.py allauth_schema` writes that result:

```bash
uv run python manage.py allauth_schema        # rewrites allauth-schema.yml
```

`get_schema()` roots its paths at the URL of allauth's own `openapi.yaml`
route, which is mounted only when `HEADLESS_SERVE_SPECIFICATION` is on. The
command turns the setting on for that one call; **it is never on in a running
deployment**, so the specification is not served on any store's host.

## What keeps it true

* CI regenerates it and fails when the committed file differs structurally
  (`allauth-schema.yml is up to date`), and the `API contract` workflow runs
  `oasdiff breaking` on it exactly as on `schema.yml`.
* `tests/integration/user/test_allauth_response_contract.py` drives our real
  endpoints (configuration, session, login, signup, login code, second factor,
  authenticators, email, providers, sessions) and validates each status and
  body against the file. The upstream document cannot know our
  `TenantHeadlessAdapter`, `SessionTokenStrategy` or settings, so where it and
  the endpoint disagree the endpoint wins and the disagreement is fixed:
  * `TenantHeadlessAdapter.get_user_dataclass` makes `username` optional: a
    customer who signs up by email has none, and `serialize_user` drops blank
    values, while allauth's dataclass says it is required.
  * the command lists every `allauth.headless.constants.Flow` value in
    `Flow.id`: the upstream enum omits `mfa_login_webauthn`,
    `mfa_signup_webauthn`, `mfa_trust` and `password_reset_by_code`, which
    responses carry.

Everything else the real endpoints disagree on is a correction in
`src/core/allauth_spec_corrections.py`, applied by the command, each naming the
allauth source it was read from: `Flow.id` lists every flow and `Flow.providers`
the provider ids of a provider flow, `meta.is_authenticated` is nullable (null
for an expired session), the passkey signup answers creation options, not
request options, `OptionalTimestamp` is really nullable (upstream put
`nullable` beside a `$ref`, where OpenAPI 3.0 ignores it), `mfa.passkey_login_enabled`
and `account.authentication_method` are declared, `POST .../recovery-codes`
answers 200, and our own `GET .../totp/svg` (`core.views.ManageTOTPSvgView`) is
added; `X-Session-Token` is a security scheme (`SessionToken`) in allauth 65.22 and
later, so our `totp/svg` operation copies the `security` of the status
operation rather than a header parameter. Every operation may also answer 503 `UnavailableResponse`
(`temporarily_unavailable`): with Redis unreachable a security control (a rate
limit, TOTP replay protection) refuses the call rather than lift itself
(`src/core/middleware/strict_cache.py`, the rule beside `STRICT_KEY_PREFIXES`
in `src/core/cache_backend.py`). The contract test also
fails on any key an endpoint serves that the document does not declare.

A new correction is a hook on the adapter where allauth offers one, otherwise
an entry in `CORRECTIONS`, and in both cases a case in the contract test that
fails without it.

## Reauthentication

A call of allauth's that needs a recent proof of identity (managing a second
factor) answers 401 with the ways this customer can give it, `data.flows`,
from
`allauth.account.internal.flows.reauthentication.get_reauthentication_flows`:

* `reauthenticate`: a customer with a usable password.
* `mfa_reauthenticate`: a customer with an authenticator.
* `reauthenticate_by_code`: a code mailed to a verified address of the
  customer (allauth 65.22; `POST .../auth/reauthenticate/code/request`,
  `.../confirm`, `.../resend`). **Off for now**:
  `ACCOUNT_REAUTHENTICATION_METHODS` in `src/settings/auth.py` leaves out
  `code`, the three endpoints answer 403, and `declare_every_flow` keeps the
  value out of `Flow.id`. The storefront live in production rejects a
  `Flow.id` it does not know (422), so the flow is switched on only after a
  storefront that completes it is live, in three releases, each one checked
  with `api-contract/api-contract.sh compare`: this document (new paths, the
  `SessionToken` security scheme, `recipients`, the enum without the value),
  then the storefront generated from the document with the value, then the
  release that deletes the setting (the document differs by that one enum
  value).

The storefront's account deletion (`user.views.account`) is a REST call
authenticated by a Knox token, which carries no session: it names the allauth
app session the customer signed in with in `X-Session-Token`, and the view
reads the reauthentication there (`_reauthentication_required`), never in a
session of another customer nor in the request's own cookie session, so a
stolen token cannot borrow someone else's fresh confirmation. Its refusal is a 403 `reauthentication_required`
with the same `flows`, so the storefront sends the customer to confirm it is
them and back.

A customer with none of the methods that are on (no password, no
authenticator) is not asked while the code method is off
(`TenantAccountAdapter.is_reauthentication_required`, deleted with the
setting): allauth up to 65.21 counted such a customer as always freshly
reauthenticated, and 65.22's refusal would leave them no way to delete their
account or add a second factor until the code method is on.

`AuthenticationMethod` keeps the titles its two code entries had before 65.22
(`keep_the_code_methods_titles`): a consumer's contract check pairs the entries
of a `oneOf` by title.
