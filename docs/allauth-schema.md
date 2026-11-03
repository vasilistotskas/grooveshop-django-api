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

A new correction is a hook on the adapter where allauth offers one, otherwise
a step in the command, and in both cases a case in the contract test that
fails without it.
