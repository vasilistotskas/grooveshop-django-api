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

An allauth call that needs a recent proof of identity (managing a second
factor) answers 401 with the ways this customer can give it, `data.flows`
(the storefront's account deletion, `user.views.account`, answers 403
`reauthentication_required` and no flows), from
`allauth.account.internal.flows.reauthentication.get_reauthentication_flows`:

* `reauthenticate`: a customer with a usable password.
* `reauthenticate_by_code`: a code mailed to a verified address of the
  customer, listed in the flow's `recipients`. `ACCOUNT_REAUTHENTICATION_METHODS`
  is left at allauth's default (`password`, `code`, `mfa`), so every
  customer has it, one without a password included. `POST
  .../auth/reauthenticate/code/request` (`email`), `.../confirm` (`code`) and
  `.../resend`; while a code is pending the flow carries `is_pending: true`.
  The mail is a type of its own, `account.reauthentication_code`
  (`UserAccountAdapter.send_mail`), so it does not call itself a sign-in code.
  Its limits are allauth's per-address `request_login_code` limit, `ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS` and
  `ACCOUNT_LOGIN_BY_CODE_SUPPORTS_RESEND`, and the per-IP rules in
  `core.middleware.allauth_ratelimit`.
* `mfa_reauthenticate`: a customer with an authenticator.

A customer with none of them (no password, no authenticator, no verified
address) is refused: allauth up to 65.21 counted such a customer as always
freshly reauthenticated, 65.22 denies. A customer who is signed in always has
a verified address (an account whose address is unverified cannot sign in),
so the code is always a way.

`AuthenticationMethod` keeps the titles its two code entries had before 65.22
(`keep_the_code_methods_titles`): a consumer's contract check pairs the entries
of a `oneOf` by title, and a renamed one reads as removed and added.
