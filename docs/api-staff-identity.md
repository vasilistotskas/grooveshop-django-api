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

Today the answer is "they don't" — administrative API routes are
`IsPlatformSuperuser`. Store operators administer their store through
the Django admin, where role-derived permissions apply properly
(`tenant.auth_backends.TenantRolePermissionBackend`). This document
describes what to build when that stops being enough.

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
