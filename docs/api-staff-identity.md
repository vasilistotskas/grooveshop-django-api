# API staff identity

**Question it answers:** how does a *store operator* get programmatic
(API) access to their own store?

Through a platform-staff token. `POST /api/v1/platform/auth/login` on the
platform host (`tenant.urls_public`) mints one and `.../auth/logout` ends it
(`PlatformStaffLoginView` / `PlatformStaffLogoutView` in
`src/tenant/staff_api.py`). The client sends it as
`Authorization: StaffBearer <token>`, which
`PlatformStaffTokenAuthentication` (`src/tenant/api_tokens.py`) resolves. The
token is a `PlatformStaffToken` (`src/tenant/models/staff_token.py`): a Knox
`AbstractAuthToken` subclass in the SHARED-only `tenant` app.

Authorization is by the caller's role in the CURRENT tenant, through the same
`tenant.auth_backends.TenantRolePermissionBackend` policy the admin uses. The
permission classes are in `src/core/api/permissions.py`:

- `StoreStaffModelPermissions` gates the store-scoped write routes (the
  viewsets that name it: product, blog, order, cart, pay_way, page_config,
  shipping, tag, builder and others).
- `StoreStaffChangePermission` gates a custom action on the `change`
  permission of the view's model.
- `IsStoreStaff` covers the routes that map to no model (for example search
  analytics).
- `IsPlatformSuperuser` is for platform-wide surfaces only.

## Why neither `IsAdminUser` nor membership alone works

### `IsAdminUser`

DRF's `IsAdminUser` is literally `request.user.is_staff`. On an API
request that flag lives on a **tenant-schema** row:

- `knox` is in `TENANT_APPS` only, so `knox_authtoken` is per-schema and
  `request.user` is always a user from *that store's* schema
  (`core.api.tokens.BoundedTokenAuthentication` documents this: cross
  tenant token replay is structurally impossible).
- Those rows are the store's **customers**, so `is_staff` or `is_superuser` on
  one is a customer-record flag, not a staff roster, and it would duplicate
  the membership model.

No API permission trusts those flags on a tenant-schema row.

### `HasTenantAccess` / membership (what the admin uses)

`UserTenantMembership.user` is a FK to `public.user_useraccount`. Its
docstring is explicit: it is a *staff grant keyed to a platform
identity*, not a customer roster.

An API session authenticates against the tenant schema, so
`get_membership(user, tenant)` would compare `user_id = user.pk` against the
**public** membership table, a primary-key comparison across schemas that
matches whichever public row happens to share the pk. Matching on email is no
safer: a shopper can register a tenant-schema account with an operator's
address.

The admin closes this with a provenance stamp
(`tenant.auth_backends.PLATFORM_IDENTITY_ATTR`), set by
`PlatformStaffBackend` when it loads a user *from the public schema*. A
customer API session never carries that stamp, because it never authenticates
against public. The staff token flow stamps it, so the API has a sound notion
of "store staff": a stamped platform identity with a staff-capable membership
in the current tenant (`tenant.membership.is_store_staff`).

## The store team admin

Staff identities live in the public schema and are shared by every store,
so anything on a store host that lists accounts lists the whole platform.
The team page (`UserTenantMembershipAdmin`) therefore never offers a
chooser of accounts to a store role:

- **Adding a person** is by exact email (`StoreTeamMemberForm`): the
  address must match one existing platform account, case aside, and the
  person is bound to the store being served. Nothing is listed, searched
  by prefix or suggested, and the store is not a field. An unknown address
  is refused with one message. The one thing this confirms is that an
  address the caller already knows holds a platform account.
- **The list** has no Store column, no Store filter and no search box on a
  store host: the join to the user table resolves to that store's shoppers
  there, not to the team. The member is read from the public schema
  (`UserTenantMembership.member`).
- **The platform console** keeps the account and store pickers.
- **A role is always chosen**: the model has no default role, forms offer
  an empty choice first (`STAFF`, `ADMIN` or `OWNER`, `TenantMembershipRole`).

### The last owner

A store must keep at least one active OWNER. `UserTenantMembership`
enforces it, so no path walks around it: `save()` (demote, deactivate,
move), `delete()`, `clean()` (a form error), and the bulk
`QuerySet.delete()` / `update()` (the admin's bulk delete, a
`filter().update()`). The check locks the tenant row, so two owners
demoting each other at once cannot both pass. A store with no active owner
already is not blocked from other edits. Deleting a whole store or a
platform identity cascades through the collector and is not blocked:
destroying a store must still work. Deactivating the owner's *account*
(`UserAccount.is_active`) is not a membership change and is not covered.

## How the API authenticates staff

The API mirrors the admin with the same three pieces.

1. **Authentication against the public schema.** `PlatformStaffTokenAuthentication`
   resolves tokens against `public`, the way `PlatformStaffBackend` does for
   the admin login. Staff tokens are stored in the **public** schema, not in
   `knox_authtoken` (which is per-tenant by design and must stay that way: it
   is what makes customer tokens un-replayable across stores).

2. **Provenance, not primary keys.** The resulting user object carries
   `PLATFORM_IDENTITY_ATTR`, exactly as `PlatformStaffBackend` sets it.
   Every downstream authorization decision keys off that stamp, so a
   tenant-schema customer sharing a pk (or an email) is never taken for a
   platform identity.

3. **Authorization from role.** With a stamped identity and
   `connection.tenant` on the request, the existing policy
   (`tenant.role_scopes` plus `TenantRolePermissionBackend`) decides, so the
   admin and the API share one source of truth for "what may a STAFF do".

### Token model: subclass `knox.models.AbstractAuthToken`

`AbstractAuthToken` is genuinely abstract (digest PK, `token_key`, user FK,
`created`, `expiry`) and subclassing it into a second concrete model is the
supported customization path (see the pinned knox in `pyproject.toml`).
`KNOX_TOKEN_MODEL` is a *swap* (one global model, like `AUTH_USER_MODEL`), so it
cannot ADD a token model: the customer token stays `knox.AuthToken` and the
staff token is the concrete subclass `tenant.PlatformStaffToken`.

The owning app is in **SHARED_APPS only** (`tenant`). That makes the table
exist only in public and its user FK target `public.user_useraccount`
**structurally**, the same mechanism that makes `knox_authtoken` per-tenant,
pointed the other way.

### The scheme keyword differs from `Bearer`

In `knox/auth.py`, once the `Authorization` keyword matches, an unrecognised
token **raises** `AuthenticationFailed` instead of returning `None`, and DRF
stops the authenticator chain on a raise. Two authenticators claiming `Bearer`
therefore cannot coexist: whichever runs first 401s the other's tokens. Staff
auth engages only on its own keyword, `Authorization: StaffBearer <token>`
(`authenticate_header`), and returns `None` for everything else, so the chain
composes cleanly on shared viewsets.

### The auth class: extends `BoundedTokenAuthentication`

`PlatformStaffTokenAuthentication` subclasses the project's
`BoundedTokenAuthentication`, so staff tokens inherit the absolute-age cap. It
overrides the token model, the lookup (wrapped in `schema_context(public)`),
the keyword, and sets `customer_tokens = False` (a staff token is not a
customer's sign-in, so a store with customer accounts off still accepts it).
It also does two things the admin flow gets implicitly:

- **Stamps `PLATFORM_IDENTITY_ATTR`** on the returned user, which lets the
  existing `TenantRolePermissionBackend` serve API requests unchanged. Its
  provenance gate excludes unstamped API sessions.
- **Re-checks `is_staff` per request**, not just at login. Knox's
  `validate_user` checks only `is_active`; the standing revocation flow
  clears `is_staff`, and a live token dies with it.

### Authorization: DRF's own `DjangoModelPermissions`

Because the stamped identity makes `user.has_perm(...)` resolve through
`TenantRolePermissionBackend`, per-action authorization is what
`rest_framework.permissions.DjangoModelPermissions` already does (HTTP
method to `add`/`change`/`delete` codename to `has_perm`).
`StoreStaffModelPermissions` subclasses it only to add the `view` permission on
reads. Object scoping stays in the querysets, as in the admin.

`UserAccount.has_perm`, `has_module_perms` and the `get_*_permissions`
methods answer only for a platform identity
(`tenant.membership.is_platform_identity`: stamped, or on the public
schema). A tenant-schema row is a customer record, so its
`is_superuser`/`is_staff` flags and any stored grants confer no Django
permission (`tests_mt/test_platform_superuser_is_schema_aware.py`).

### Issuance and revocation

The login endpoint is on the PLATFORM host only (`tenant.urls_public`). It
calls `PlatformStaffBackend.authenticate_staff()` and requires an active
staff-capable membership before minting. The storefront's headless login can
never mint one: different URLconf, different backend, the same deliberately
inert wall the admin uses. Revocation is deactivating the membership and
clearing `is_staff`, plus token deletion; the per-request `is_staff` re-check
makes the flag clearing take effect immediately even for outstanding tokens.

### Audit attribution across schemas

A staff member's id is a public-schema id, so a `history_user` or a
`changed_by` that was a foreign key to the tenant's user table would break or
mislead on every staff write. Those columns are plain ids resolved in `public`
(see "Staff members named on store rows" below).

### Why not OAuth2 / allauth.idp

`allauth.idp.oidc` runs here, but in **TENANT_APPS**: it is a per-store IdP
whose resource owners are that store's CUSTOMERS (it is what the agent surface
authenticates against). Staff are public-schema identities, so the existing IdP
structurally cannot issue for them, and a second, public-schema IdP would be
real extra surface. PAT-style staff tokens cover operator scripting and
first-party clients. Third-party delegated access would need OAuth2
client-credentials on a public-schema IdP, with scopes mapping onto
`tenant.role_scopes`, the same policy source.

### Consequences

- Store operators hold **two** credentials: a customer account in their
  store's schema (if they shop there) and a platform staff identity. That is
  inherent to one identity per email living in `public`, and is the same split
  the admin has.
- Every administrative route's permission derives from role rather than a
  blanket superuser check.
- The storefront can never obtain a staff token; the login paths stay
  separate, as `PlatformStaffBackend.authenticate()` (deliberately inert)
  already enforces for the admin.

## Second factor for platform superusers (admin login)

A platform superuser reaches every store and the control plane, so the admin
login (`admin.forms.PlatformAdminAuthenticationForm`, shared by the platform
and the store admin) asks for a second factor after the password, **only
when `user.is_superuser`**. Store admins and staff stay password-only.

- **What:** an authenticator (TOTP) code or an unused recovery code, checked
  with allauth's own helpers (`allauth.mfa`: code validation, replay
  protection, recovery codes and their encryption). `src/admin/staff_mfa.py`
  only decides when a code is required and carries the enrolment.
  `allauth.mfa` is in `SHARED_APPS` as well as `TENANT_APPS`, so the
  authenticator rows sit in the public schema next to the platform user.
- **A step-by-step sign-in held in the session.** The password is posted
  once; for a superuser the form then stores a pending sign-in in the
  session (`staff_mfa.PENDING_SESSION_KEY`: user id, step, when the step
  began) and shows the next step, never re-posting the password. Steps:
  `password` -> `code` (enrolled), or `password` -> `enrol` -> `recovery`
  (not enrolled yet). A step change is not an error: only a wrong
  password or code, a lockout or an expired sign-in is. The pending
  sign-in ends on success, on "use another account", when it expires
  (`STAFF_MFA_PENDING_SIGN_IN_SECONDS`, default ten minutes, restarted by
  each step), and whenever another password is submitted, so it cannot be
  replayed or inherited by a different user.
- **First login enrols.** A superuser with no authenticator is shown a
  secret (QR code and text) and asks for a code from the app. A correct
  code activates the authenticator and shows the ten recovery codes once
  (copy and download buttons); confirming them completes the same
  sign-in, with no second login. The code that enrolled is spent. The
  recovery codes sit in the pending sign-in until confirmed, so a reload
  does not lose them.
- **Guessing is bounded:** five code attempts per user in fifteen minutes
  (cache), then even a right one is refused. The attempt is counted BEFORE the
  code is checked (`staff_mfa.register_attempt`), so parallel requests cannot
  each get a guess; a right code clears the count. The count is shared by the
  admin login and the token endpoint, and is kept under a `global:strict:`
  key, so every host (the platform's and each store's) draws on one allowance
  and a cache that cannot count stops the sign-in instead of lifting the lock.
- **Lost authenticator, no recovery codes:** `manage.py reset_staff_mfa <email>`
  (run with cluster access) removes the authenticators and any
  lockout; the next login enrols a new one.
- **The token endpoint asks the same factor.** `POST /api/v1/platform/auth/login`
  takes `email`, `password` and, for every user `staff_mfa.requires_mfa` covers
  (a superuser), a `code` (authenticator or recovery code), checked by
  `staff_mfa.verify_second_factor`, the helper the admin form's `code` step
  uses, with the same lockout and failure count. A superuser who has not set up
  an authenticator gets `403 mfa_enrolment_required`: enrolment needs the QR
  step, which stays in the admin login, so sign in there once first. Other
  answers: `401 mfa_code_required` (none sent), `401 mfa_wrong_code`,
  `429 mfa_locked`. A token already issued keeps working. WebAuthn/passkeys
  are not offered for the admin login.

## Sign-in attempts (admin login and token endpoint)

allauth's limiter covers `/_allauth/` only, so attempts at the Django admin
login and at the token endpoint are counted by
`src/tenant/staff_login_limits.py`. Both reach the password check through
`PlatformStaffBackend.authenticate_staff`, which does the counting, so there is
no second copy.

- **Counted first.** `register_attempt` increments (`cache.count_in_window`:
  one Redis script that adds one and starts the window) before the password is looked at,
  and raises `StaffLoginLocked` when the result is over a budget, without
  checking the password. Parallel requests each get their own number, so the
  budget is not "the limit plus the requests in flight".
- **Account + proven address** (`MAX_FAILURES_PER_ACCOUNT_ADDRESS`, 5): the
  strict budget. A lock here refuses only that address, so someone who knows a
  staff email cannot lock the owner out from the owner's own address. A
  success clears it; so do a password reset (`StaffSetPasswordForm.save`, which
  clears BEFORE it saves, so a cache that cannot be written fails the reset
  before anything changes) and `reset_staff_mfa`.
- **Account-wide ceiling** (`MAX_FAILURES_PER_ACCOUNT`, 50): bounds a guessing
  run spread over many addresses. Past it the account is locked for everyone
  until the window ends. That is the remaining denial-of-service bound: a
  caller who knows a staff email can lock it for everyone, but needs 50
  attempts spread over addresses in the window to do so (a single address is
  stopped after 5), and the owner is unlocked by a password reset or
  `reset_staff_mfa`. It is the only account limit for a request without a
  proven address.
- **Per proven address** (`MAX_FAILURES_PER_IP`, 20): a run of guesses across
  accounts. A success does not clear it, or one valid login of the attacker's
  own would reset a guessing run. An address is `edge_client_ip` (the
  request carried our edge's `X-Origin-Verify`); IPv6 counts on its /64, IPv4
  on the full address. Without the proof (a domain outside our Cloudflare
  zones, a missing or rotated secret, a direct call to a node) the only
  address known is the proxy peer, a Cloudflare edge node or SNAT gateway
  shared by everyone behind it, so no address counter applies: 20 wrong
  passwords from one place would lock every staff member out.
- **Existence-neutral.** The account key is the submitted identifier,
  lower-cased and hashed whether or not it names an account, so a locked
  unknown address answers like a locked known one.
- **A lock refuses even the right password**: the admin form shows "Too many
  failed sign-in attempts. Try again later." and the endpoint answers `429
  login_locked`. Refused attempts do not extend it; it ends when the window
  that began with the first attempt does.
- **Keys** are `global:strict:` (see above), env-tunable as
  `STAFF_LOGIN_MAX_FAILURES_PER_ACCOUNT_ADDRESS`,
  `STAFF_LOGIN_MAX_FAILURES_PER_ACCOUNT`, `STAFF_LOGIN_MAX_FAILURES_PER_IP`,
  `STAFF_LOGIN_WINDOW_SECONDS`. The endpoint's `staff_login` throttle (10 an
  hour) still sits in front.

### If the cache is down

Staff sign-in is refused on purpose while the attempt counters cannot be read
or written (`StaffLoginUnavailable`, raised for the cache's own
`REDIS_UNAVAILABLE` errors on the `strict:` keys): the admin form shows "Sign-in
is temporarily unavailable, try again shortly." and the token endpoint answers
`503 staff_login_unavailable`, with one ERROR log line per attempt (no
username) and the password never checked. Otherwise an attacker could lift the
limits by overloading Redis. Customer sign-in behaves the same, because
allauth's limiter keys are strict too.

The way in during a Redis incident is the cluster, not the admin: run
`manage.py` with `kubectl --context grooveshop exec` in a backend pod.
`manage.py changepassword <email>` sets a staff password (it runs in the public
schema, where staff identities live, and needs no cache). `manage.py
reset_staff_mfa <email>` also clears the account's attempt counters, so it needs
the cache; with Redis down, restore Redis first.

## Forgotten password (admin login)

The admin login offers **"Forgotten your password or username?"** on the
platform and on every store host (`src/admin/password_reset.py`, mounted by
`StaffPasswordResetMixin` on both admin sites). It is **Django's own**
`PasswordResetView` / `PasswordResetConfirmView`, not allauth's reset flow.
allauth's reset belongs to the shopper's account (a tenant-schema
`EmailAddress`, the storefront's login, headless URLs); the admin login never
reads an allauth session, and a staff member's password is a Django password
on their public-schema row (`PlatformStaffBackend`), so allauth's flow would
look in the wrong place. allauth's own docs say its rate limits do not
protect the Django admin login either.

- **Public schema.** The form's lookup and the save of the new password run in
  `public`; a store host binds the store's schema, whose `UserAccount` rows are
  shoppers.
- **Who may ask.** `can_reset_here`: an active staff account with a usable
  password that could sign in on THIS host: a superuser, or on a store host an
  active staff-capable membership in that store. The platform host serves
  superusers alone. The confirm view re-checks it, so a link outlives neither a
  deactivation nor a removed role.
- **No enumeration.** Every accepted request answers the same redirect and
  page. The request does not look the address up: it queues
  `tenant.tasks.send_staff_password_reset_email` once, and the task finds the
  accounts, so a match is not slower either. Budgets
  (`settings.STAFF_PASSWORD_RESET`): per client address (`client_ip_or_peer`;
  past it the answer is 429, which says nothing about any address) and per
  target address (past it the request is dropped silently, same answer). Both
  fail closed when the cache cannot count, because the form makes us mail an
  address the caller chose.
- **Token.** Django's `PasswordResetTokenGenerator`: bound to the password
  hash and `last_login`, so a link works once and dies when the password
  changes; it expires after `PASSWORD_RESET_TIMEOUT` (an hour by default).
  Django swaps the token for a session marker on the first visit, so it does
  not leak in a `Referer`.
- **Mail.** `platform.staff_password_reset` (`src/tenant/emails.py`, see
  `docs/emails/types.md`): the PLATFORM sends it, from `"{SITE_NAME}"
  <DEFAULT_FROM_EMAIL>` under `public`, in the store's `default_locale` (the
  project default on the platform host). The subject and the heading name the
  store, read in its schema before the send (the mail is built under
  `public`, which knows only the platform); the logo, footer and contact
  address are the platform's. The link points at the host the request came
  in on, which the tenant middleware already matched to one of the store's
  own domains.
- **MFA is not bypassed.** The reset sets a password and nothing else:
  `post_reset_login` stays off, so nobody is signed in, and the authenticators
  are untouched, so a platform superuser still meets the code (or the
  enrolment) step of the login form next time. A mailbox alone never yields a
  session. The reset also ends the account's staff API tokens
  (`PlatformStaffToken`), which the old password could have minted.

## Builder session tokens

A staff token has a `scope`. `api` is the token the platform login mints
(renewed by use, any staff route the role allows). `builder` is minted by
the page builder's ticket exchange (`docs/builder/sign-in-and-defaults.md`, "Signing in to the
editor"): it is never renewed, ends at its expiry, and
`PlatformStaffTokenAuthentication` refuses it outside the `builder` URL
namespace (`staff_token_scope`). The role and `is_staff` re-checks apply to
both.

## Staff members named on store rows

A staff member is a **public-schema** account; the rows they act on (a
product, a shipment, a stock movement, a gift-card adjustment) live in the
**store's schema**, whose own `user_useraccount` holds the store's
*shoppers*. A foreign key from such a row to `UserAccount` therefore points
the staff id at the wrong table. PostgreSQL cannot express the cross-schema
constraint, so such a key either rejects a staff member the store has no copy of
(`ForeignKeyViolation`), or, when a shopper holds that id, silently accepts
it and **shows the shopper as the actor**
(`tests_mt/test_staff_actor_resolution.py`).

So these columns are plain ids with no relation (`src/tenant/staff_actors.py`):

- `x_id` is a nullable `BigIntegerField`; `x` is a `StaffActor` property that
  resolves the id in `public`: the account, `None` when empty, or an
  `UnknownActor` (renders empty, keeps the pk) for a deleted account.
- There is no join to get wrong, so `select_related("x")` does not apply; a
  page of rows resolves in one public query with
  `prefetch_related("x")` (`BaseModelAdmin.staff_actor_fields` does it for
  changelists and datasets).
- Only an identity stamped by the staff backend is stored (`staff_id`). A
  shopper acting through the API leaves the column empty rather than naming
  an unrelated staff member.
- simple_history models use `history_user_id_field` with
  `history_user_getter` / `history_user_setter` (documented simple_history
  options), so `history_user` resolves the same way.
- `StockLog` keeps both kinds of actor apart: `performed_by` is the shopper
  behind a reservation (a real relation to the store's users),
  `performed_by_staff_id` is the staff member. `StockLog.actor` is whichever is
  set.

Columns covered: every attribute declared `StaffActor()` (`rg 'StaffActor\(\)' src/`,
for example `Product.changed_by`, `AcsPickupList.issued_by`,
`BusinessProfile.reviewed_by`, `GiftCardTransaction.created_by`) and every
simple_history `history_user` that sets `history_user_id_field`. Where a column
was converted from a foreign key, its migration changes Django's state only (the
column keeps its name, type and data) and drops the constraint in store schemas
(`tenant.migration_helpers.drop_actor_fk`), as `django_admin_log.user_id` is
handled by `admin.log_actors`. A new column that records a staff member uses
`StaffActor`, never a foreign key to `UserAccount`.
