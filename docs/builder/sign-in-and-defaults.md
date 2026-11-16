# Page builder: signing in and the starter content

## Signing in to the editor

The editor runs on the store's own host, where nobody is signed in as staff
(staff are public-schema identities; a storefront session is a customer's).
`src/builder/staff_session.py`:

1. Store admin, **Content -> Edit storefront** (`admin:edit-storefront`,
   `src/builder/admin_views.py`; for a holder of `builder.view_builderdocument`,
   so every role: ADMIN and OWNER edit in the editor, STAFF open it read only,
   and the session's `role` tells the editor which; `never_cache`).
   It stores the staff id in Redis under a random key for
   `BUILDER_STAFF_TICKET_TTL_SECONDS` (a minute) and redirects to
   `https://<store>/_builder?ticket=<ticket>`.
2. The storefront's SERVER posts `{ticket}` to `POST /api/v1/builder/
   staff-session` and answers `200 {token, expiresAt, staff: {id, name,
   role}}`; any problem is `400 invalid_ticket`, one body whatever the
   cause. `role` is the membership role in this store, or `superuser`.
3. The storefront keeps the token in its own HttpOnly cookie and sends it as
   `Authorization: StaffBearer <token>`. `DELETE builder/staff-session`
   (that token) ends it.

Security properties, each with a test:

- **Single use.** The ticket is minted and spent by `src/core/tickets.py`, the
  helper the WebSocket ticket uses too: Redis `GETDEL`, one round trip, so of
  two requests with the same ticket one wins. Tickets live under the `strict:`
  cache namespace, so an unreachable Redis raises instead of failing open.
  Intercepting it after use gains nothing, and unused it dies in a minute.
- **Checked when spent, not when minted.** The person must still be an
  active staff account with the right to see this store's pages (the role
  backend, through the provenance stamp), so a membership lost in between is
  `invalid_ticket`. A STAFF session is read only: `StoreStaffModelPermissions`
  refuses every write by role (`403`), exactly as for a signed-in STAFF.
- **One store.** The cache key function puts the schema in every key, so a
  ticket minted on store A finds nothing on store B
  (`tests_mt/test_builder_staff_ticket.py`).
- **A token that stays small.** It is a `PlatformStaffToken` with
  `scope = builder` (`tenant.models.StaffTokenScope`): knox's auto-refresh
  would push any token to its seven-day lifetime, so
  `PlatformStaffTokenAuthentication` does not renew a builder token (it ends
  at `BUILDER_STAFF_SESSION_TTL_SECONDS`, a working day) and refuses it on
  every route outside the `builder` URL namespace (`401 staff_token_scope`).
- **Revocation.** The authenticator re-checks `is_staff` and the role in this
  store on every request, so removing the membership, clearing `is_staff` or
  deactivating the account ends a live session at once; a password reset
  deletes the account's staff tokens (`src/admin/password_reset.py`); logout
  deletes it.
- **A ticket is in a URL**, so it can reach a log or a history entry; that is
  why it lives a minute and is single use. The storefront should trade it at
  once and redirect without it.
- The sign-in route has its own throttle scope (`builder_session`): its one
  caller is our storefront, for every store, so the per-person budget of the
  platform login does not fit; a ticket is 256 random bits, so the limit
  bounds load, not guessing.

## What is copied from the storefront

The storefront owns the blocks, so these are pinned COPIES of its
files (the media service's image limits are copied the same way, see
[`docs/image-uploads.md`](../image-uploads.md)):

| Source | Copy | Used by |
|---|---|---|
| `test/fixtures/builder/catalogue/` | `src/builder/catalogues/` | the block catalogue as each design is served it (`<design>.json`; `default.json` for every store whose schema no design names): `builder.seeding` derives the seeds against `default.json`, and the seeds, demo pages and real-catalogue tests run against each design's |
| `test/fixtures/builder/seeds/` | `src/builder/seeds/` | `builder.seeding`: application data |

`tests/fixtures/upstream-sources.lock.json` names each owner's commit and
the git id of each source (the tree id of a directory, the blob id of a file).
Only `.github/scripts/sync_upstream_sources.py` writes the copies and the
lock; `tests/unit/core/test_upstream_sources_pin.py` fails when a copy is
not the pinned one, and the weekly `upstream-sources.yml` workflow fails when
an owner's `main` has moved on. A refresh is: run the script, replay the
builder tests, commit.

## Default documents for a new store

`src/builder/seeding.py` loads `src/builder/seeds/*.json` (each `{key, document,
i18n}`). Provisioning (`tenant.provisioning`, step "page builder")
stores each as a document whose draft equals its published content, one
version, and ONE release (number 1) holding them all; today that is
`template:home`, 11 sections with fixed ids. It calls no service, so a
storefront outage cannot leave a store without its homepage, and a store that
already has builder data is left alone. What stands in for validation is
`tests/unit/builder/test_seeds.py`, which validates every seed against the
catalogue snapshot and checks the values `seeding` derives (block types,
references) equal what validation derives.

## The demo store's pages

`src/devtools/demo_pages/*.json` are the demo store's pages and menus (home,
about, contact, feedback, the footer and the mobile menu) in the same
`{key, document, i18n}` shape, exported from production's demo store. What
differs per store is a placeholder: `{{asset:<key>}}` for an image (the
committed demo asset, copied into the store's media) and
`{{product:<slug>}}` for a product id. `manage.py seed_demo_store` (step
`pages`, after products and content pages) resolves them and calls
`seeding.seed_with_content`, which releases them in ONE release (note "Demo
content") only on a store whose builder data is nothing or exactly the
untouched defaults: a store where anyone saved a draft, named a save,
scheduled, deleted a page or published is left alone and the step reports
`pages_left_alone`. A staging refresh restores production first, so there
it never overwrites the demo pages the editor keeps. Unlike the defaults,
this content names the store's records, so every document is validated
against the store's catalogue, references included, like a publish, and
nothing is written when the storefront does not answer.
`tests/unit/devtools/test_demo_pages.py` holds the files to the pinned real
catalogue.

## Not here yet

The `builder_block_usage` command that will gate removing a block type is its
own change.
