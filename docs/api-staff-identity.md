# API staff identity

**Status:** implemented in v3.4.0 (2026-08-22). This document is kept as
the design record; the sections below describing the design as "deferred"
are historical. The shipped implementation lives in `tenant/staff_api.py`
(`PlatformStaffLoginView` / `PlatformStaffLogoutView`), `tenant/api_tokens.py`
(`PlatformStaffTokenAuthentication`), and the `PlatformStaffToken` model
(`tenant/migrations/0017_platformstafftoken.py`) — a Knox `AbstractAuthToken`
subclass in the SHARED-only `tenant` app, exactly as prescribed below.
A follow-up (`fix(audit): stop cross-schema attribution FKs from breaking
staff writes`) closed the cross-schema-FK prerequisite noted later in this
doc.

**Question it answers:** how does a *store operator* get programmatic
(API) write access to their own store?

They do, via a platform-staff token. **This paragraph used to say the
opposite** — that administrative API routes were all `IsPlatformSuperuser`
and operators had to use the Django admin — which stopped being true when
the design below shipped.

What is live today: `StoreStaffModelPermissions` gates store-scoped write
routes across 21 view modules (product, blog, order, cart, pay_way,
page_config, shipping, tag, …), deriving permissions from the caller's role
in the CURRENT tenant through the SAME
`tenant.auth_backends.TenantRolePermissionBackend` policy the admin uses.
`IsStoreStaff` covers the non-model routes (for example search analytics).
`IsPlatformSuperuser` is now reserved for genuinely platform-wide surfaces,
not for everything administrative.

The rest of this document is the original design record.

## Why the obvious approaches do not work

### `IsAdminUser` (what we had)

DRF's `IsAdminUser` is literally `request.user.is_staff`. On an API
request that flag lives on a **tenant-schema** row:

- `knox` is in `TENANT_APPS` only, so `knox_authtoken` is per-schema and
  `request.user` is always a user from *that store's* schema
  (`core.api.tokens.BoundedTokenAuthentication` documents this — cross
  tenant token replay is structurally impossible).
- Those rows are the store's **customers**. After the multi-tenant
  cutover copied users id-preserving, several customer rows carried
  `is_staff=True` purely as residue, which handed those accounts
  administrative API rights over the catalogue.

Nobody manages that flag as a staff roster, and it duplicates the
membership model. It is not a foundation to build on.

### `HasTenantAccess` / membership (what the admin uses)

`UserTenantMembership.user` is a FK to `public.user_useraccount`. Its
docstring is explicit: it is a *staff grant keyed to a platform
identity*, not a customer roster.

An API session authenticates against the tenant schema, so
`get_membership(user, tenant)` compares `user_id = user.pk` against the
**public** membership table — a primary-key comparison across schemas.
It matches whichever public row happens to share the pk. The pks line
up today only because the cutover copied users id-preserving;
post-cutover signups diverge. Matching on email is no safer: a shopper
can register a tenant-schema account with an operator's address.

The admin closes this with a provenance stamp
(`tenant.auth_backends.PLATFORM_IDENTITY_ATTR`), set by
`PlatformStaffBackend` when it loads a user *from the public schema*. An
API session can never carry that stamp, because it never authenticates
against public.

**So the API has no sound notion of "store staff" at all.** That is the
gap, and it cannot be patched with a permission class.

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

## The design, when it is needed

Mirror the admin exactly. The admin already solved this problem; the API
needs the same three pieces rather than a parallel invention.

1. **Authentication against the public schema.** A staff API
   authentication class that resolves credentials/tokens against
   `public`, the way `PlatformStaffBackend` does for the admin login.
   Staff tokens are stored in the **public** schema, NOT in
   `knox_authtoken` (which is per-tenant by design and must stay that
   way — it is what makes customer tokens un-replayable across stores).

2. **Provenance, not primary keys.** The resulting user object carries
   `PLATFORM_IDENTITY_ATTR`, exactly as `PlatformStaffBackend` sets it.
   Every downstream authorization decision keys off that stamp, so a
   tenant-schema customer sharing a pk (or an email) can never be
   mistaken for a platform identity.

3. **Authorization from role.** With a stamped identity and
   `connection.tenant` on the request, reuse the existing policy —
   `tenant.role_scopes` plus `TenantRolePermissionBackend` — rather than
   re-encoding "what may a STAFF do" a second time. A DRF permission
   class that asks the role layer keeps one source of truth, so the
   admin and the API cannot drift.

## Validated against the machinery (2026-08-22)

The design above was checked against the installed libraries and the
live codebase, not just reasoned about. Findings, several of which are
binding constraints rather than suggestions:

### Token model: subclass `knox.models.AbstractAuthToken`

Verified in knox 5.1.0: `AbstractAuthToken` is genuinely abstract
(digest PK, `token_key`, user FK, `created`, `expiry`) and subclassing
it into a second concrete model is the supported customization path.
`KNOX_TOKEN_MODEL` is a *swap* (one global model, like
`AUTH_USER_MODEL`) — it cannot ADD a token model, so the customer token
stays `knox.AuthToken` and the staff token is a new concrete subclass,
e.g. `tenant.PlatformStaffToken`.

The owning app must be in **SHARED_APPS only** (`tenant` qualifies).
That makes the table exist only in public and its user FK target
`public.user_useraccount` **structurally** — the same mechanism that
makes `knox_authtoken` per-tenant, pointed the other way.

### The scheme keyword MUST differ from `Bearer` — hard constraint

Verified in `knox/auth.py`: once the `Authorization` keyword matches,
an unrecognised token **raises** `AuthenticationFailed` — it does not
return `None` — and DRF stops the authenticator chain on a raise. Two
authenticators claiming `Bearer` therefore cannot coexist: whichever
runs first 401s the other's tokens. Staff auth engages only on its own
keyword (e.g. `Authorization: StaffBearer <token>`, via
`authenticate_header`), returns `None` for everything else, and the
chain composes cleanly on shared viewsets.

### The auth class: extend `BoundedTokenAuthentication`, plus two musts

Subclass the project's `BoundedTokenAuthentication` so staff tokens
inherit the absolute-age cap, overriding: the token model, the lookup
(wrapped in `schema_context(public)`), and the keyword. Two additions
the admin flow gets implicitly but a token flow must do explicitly:

- **Stamp `PLATFORM_IDENTITY_ATTR`** on the returned user — this is
  what lets the EXISTING `TenantRolePermissionBackend` serve API
  requests unchanged. Its provenance gate currently excludes API
  sessions precisely because nothing stamps them; a stamping
  authenticator composes with it for free. No second policy engine.
- **Re-check `is_staff` per request**, not just at login. Knox's
  `validate_user` checks only `is_active`; the standing revocation flow
  clears `is_staff`, and a live token must die with it.

### Authorization: DRF's own `DjangoModelPermissions`, not custom code

Because the stamped identity makes `user.has_perm(...)` resolve through
`TenantRolePermissionBackend`, per-action authorization is exactly what
`rest_framework.permissions.DjangoModelPermissions` already does (HTTP
method → `add`/`change`/`delete` codename → `has_perm`). Subclass it
only to add the `view` permission on reads. Object scoping stays in the
querysets, as in the admin.

`UserAccount.has_perm`, `has_module_perms` and the `get_*_permissions`
methods answer only for a platform identity
(`tenant.membership.is_platform_identity`: stamped, or on the public
schema). A tenant-schema row is a customer record, so its
`is_superuser`/`is_staff` flags and any stored grants confer no Django
permission (`tests_mt/test_platform_superuser_is_schema_aware.py`).

### Issuance and revocation

A login endpoint on the PLATFORM host only (`tenant.urls_public`),
calling `PlatformStaffBackend.authenticate_staff()` and requiring an
active staff-capable membership before minting. The storefront's
headless login can never mint one — different URLconf, different
backend, same deliberate-inert wall the admin uses. Revocation is the
existing flow (deactivate membership + clear `is_staff`) plus token
deletion; the per-request `is_staff` re-check above makes the flag
clearing take effect immediately even for outstanding tokens.

### Blocking prerequisite found: audit attribution breaks cross-schema

Validating staff WRITES surfaced a latent bug that exists in the ADMIN
today, independent of this design: `simple_history`'s `history_user`
and `product.changed_by` are FKs to the user table, which on a tenant
schema is the TENANT copy. `HistoryRequestMiddleware` attributes writes
to `request.user` — a public identity. It works today only because the
cutover mirrored users id-preserving; a post-cutover platform staff
member (public-only, no tenant row) saving any historied tenant model
gets an FK violation at INSERT. Under design B every staff API write
hits this path.

Fix (prerequisite, and worth doing regardless): simple-history's
`user_db_constraint=False` (verified present in the installed version)
and `db_constraint=False` on `changed_by` — the exact pattern this
project already uses for the cross-schema `loyalty_tier` FK — plus
recording the actor's email in `history_change_reason` so attribution
survives even where the id resolves to nothing.

### OAuth2 / allauth.idp — evaluated and deliberately NOT chosen (yet)

`allauth.idp.oidc` already runs here, but in **TENANT_APPS**: it is a
per-store IdP whose resource owners are that store's CUSTOMERS (it is
what the agent surface authenticates against). Staff are public-schema
identities, so the existing IdP structurally cannot issue for them, and
standing up a second, public-schema IdP is real surface. PAT-style
staff tokens cover triggers 1–2 (operator scripting, first-party
clients). If trigger 3 (third-party delegated access) materialises,
layer OAuth2 client-credentials on a public-schema IdP THEN — scopes
mapping onto `tenant.role_scopes`, same policy source.

### Consequences to accept

- Store operators would hold **two** credentials: a customer account in
  their store's schema (if they shop there) and a platform staff
  identity. That is inherent to one-identity-per-email living in
  `public`, and is the same split the admin already has.
- Every administrative route needs its permission reconsidered against
  role rather than a blanket superuser check — roughly the 23 view
  modules that previously used `IsAdminUser`.
- The storefront must never be able to obtain a staff token; the login
  paths stay separate, as `PlatformStaffBackend.authenticate()`
  (deliberately inert) already enforces for the admin.

## Why it is deferred

Verified 2026-08-21: these routes have **no first-party consumer**. The
storefront never writes catalogue resources (no POST/PUT/DELETE under
`server/api/product`, `tag`, `region`, `country` — its writes are
customer actions like comments and likes), and the agent gateway only
reads them (`/product*`, `/pay_way`, `/search/*`,
`/shipping/acs/stations/nearest`) while its writes are customer cart and
order operations. `page_config`'s own comment records that nothing
consumes its admin routes yet.

Building a second staff-identity system for an API with no caller is
speculative. `IsPlatformSuperuser` removes the exposure now at zero cost
to functionality, and this design stays available the moment a real
caller appears — a headless admin, an operator mobile app, or bulk
import/export tooling.

## Trigger to revisit

Any of:

- a store operator needs to script catalogue changes (bulk import,
  price feeds, PIM sync);
- an operator-facing client is built that is not the Django admin;
- a third party is granted delegated write access to one store.

## Second factor for platform superusers (admin login)

A platform superuser reaches every store and the control plane, so the admin
login (`admin.forms.PlatformAdminAuthenticationForm`, shared by the platform
and the store admin) asks for a second factor after the password, **only
when `user.is_superuser`**. Store admins and staff stay password-only.

- **What:** an authenticator (TOTP) code or an unused recovery code, checked
  with allauth's own helpers (`allauth.mfa`: code validation, replay
  protection, recovery codes and their encryption). `admin/staff_mfa.py`
  only decides when a code is required and carries the enrolment.
  `allauth.mfa` is in `SHARED_APPS` as well as `TENANT_APPS`, so the
  authenticator rows sit in the public schema next to the platform user.
- **First login enrols.** A superuser with no authenticator gets no session:
  the form shows a secret (link and QR code) and asks for a code from the
  app. A correct code activates the authenticator and shows the ten recovery
  codes once; the next login asks for a code. The code that enrolled is
  spent.
- **Guessing is bounded:** five wrong codes per user lock further tries for
  fifteen minutes (cache), even a right one.
- **Lost authenticator, no recovery codes:** `manage.py reset_staff_mfa <email>`
  (run with cluster access) removes the authenticators and any
  lockout; the next login enrols a new one.
- **Not covered:** platform staff API tokens (`PlatformStaffToken`) are issued
  after a login and are a separate surface; a token already issued keeps
  working. WebAuthn/passkeys are not offered for the admin login yet.

## Forgotten password (admin login)

The admin login offers **"Forgotten your password or username?"** on the
platform and on every store host (`admin/password_reset.py`, mounted by
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
- **Mail.** Rendered in the task from the store's `default_locale` (the
  project default on the platform host), sent from `tenant_from_email()` (the
  store's verified sender, else the platform envelope under the store's name).
  The link points at the host the request came in on, which the tenant
  middleware already matched to one of the store's own domains.
- **MFA is not bypassed.** The reset sets a password and nothing else:
  `post_reset_login` stays off, so nobody is signed in, and the authenticators
  are untouched, so a platform superuser still meets the code (or the
  enrolment) step of the login form next time. A mailbox alone never yields a
  session. The reset also ends the account's staff API tokens
  (`PlatformStaffToken`), which the old password could have minted.

## Staff members named on store rows

A staff member is a **public-schema** account; the rows they act on (a
product, a shipment, a stock movement, a gift-card adjustment) live in the
**store's schema**, whose own `user_useraccount` holds the store's
*shoppers*. A foreign key from such a row to `UserAccount` therefore points
the staff id at the wrong table. PostgreSQL cannot express the cross-schema
constraint, so it either rejected a staff member the store has no copy of
(`ForeignKeyViolation`), or, once a shopper held that id, silently accepted
it and **showed the shopper as the actor**. Reproduced in
`tests_mt/test_staff_actor_resolution.py`.

So these columns are plain ids with no relation (`tenant/staff_actors.py`):

- `x_id` is a nullable `BigIntegerField`; `x` is a `StaffActor` property that
  resolves the id in `public`: the account, `None` when empty, or an
  `UnknownActor` (renders empty, keeps the pk) for a deleted account.
- There is no join to get wrong. `select_related("x")` is gone by
  construction; a page of rows resolves in one public query with
  `prefetch_related("x")` (`BaseModelAdmin.staff_actor_fields` does it for
  changelists and datasets).
- Only an identity stamped by the staff backend is stored (`staff_id`). A
  shopper acting through the API leaves the column empty rather than naming
  an unrelated staff member.
- simple_history models use `history_user_id_field` with
  `history_user_getter` / `history_user_setter` (documented simple_history
  options), so `history_user` resolves the same way.
- `StockLog` had both writers in one column, so it is split:
  `performed_by` is the shopper behind a reservation (a real relation to the
  store's users), `performed_by_staff_id` is the staff member. `StockLog.actor`
  is whichever is set. The migration moves the rows the admin stock-edit signal
  wrote (the only staff writer then) across.

Columns covered: `Product.changed_by` and every history `history_user`
(`Product`, `ShippingRate`, `AcsShipment`, `BoxNowShipment`),
`AcsShipment.cod_received_outside_acs_by`, `AcsPickupList.issued_by`,
`AcsCodPayout.reconciled_by`, `BusinessProfile.reviewed_by`,
`GiftCardTransaction.created_by`, `PointsTransaction.created_by`,
`StockLog.performed_by_staff_id`. The migrations change Django's state only
(the columns keep their name, type and data, so the release still serving is
unaffected); the last three real foreign keys are dropped in store schemas
(`tenant.migration_helpers.drop_actor_fk`), as `django_admin_log.user_id` was
(`admin.log_actors`). A new column that records a staff member uses
`StaffActor`, never a foreign key to `UserAccount`.
