# Storefront page builder (`builder/`)

A store's theme, section groups (header, footer, ...), templates and pages
are **documents**: versioned JSON that the store's staff edit in the
storefront's visual editor and publish together. Source: `builder/`
(`models.py`, `services.py`, `validation.py`, `storefront.py`,
`references.py`, `published.py`, `views.py`, `rendering.py`,
`media_services.py`, `media_api.py`), the
`builder` cache surface in `core/cache/surfaces.py`,
`tenant/role_scopes.py`. The code wins over this page.

`builder` is a tenant app: every table lives in the store's schema, with its
backups, staging refresh and destroy. The editor and the block catalogue are
the storefront's; Django stores, validates, versions and publishes.

## The model

- `BuilderDocument`: `kind` (`theme`, `group`, `template`, `page`) and a
  unique `key` (`theme`, `group:<name>`, `template:<name>`, `page:<slug>`;
  `models.kind_of_key`). `draft` (the working copy), `draft_i18n` (the
  per-language overlay), `draft_revision` (the `If-Match` token),
  `published_revision` (the revision the current release carries, empty when
  unknown: a draft with another revision has unpublished changes),
  `draft_catalogue_hash`, `updated_by_staff_id`. Only pages are deleted
  (soft); the other kinds are fixed keys.
- `BuilderVersion`: an immutable snapshot (`data`, `i18n`), numbered per
  document, with the derived `block_types` (`type@version`, GIN-indexed, for
  the usage gate that will guard removing a block type), `refs`,
  `catalogue_hash`, `content_hash` (sha256 of the canonical encoding) and
  `size_bytes`. `kind` is `checkpoint` (a named save) or `publish`.
- `BuilderRelease`: immutable, a monotonic `number` and a map
  `{document key: version id}`. It is the only thing the public storefront
  reads. A publish is the previous release's map with the published keys
  repointed; a rollback is a new release with an older map
  (`rolled_back_from`). Releases are never deleted.
- `BuilderReference`: what the CURRENT release points at (kind, target,
  document, node path), rebuilt on every release, so the product, category
  and page admins can say where a thing is used.

Autosaves never create versions. Restoring a version always lands as a
draft. Staff are public-schema identities, so every actor column is a plain
id read through `StaffActor`.

## A document

```json
{
  "settings": {"seoTitle": "..."},
  "sections": {
    "hero_1": {
      "type": "hero@2",
      "disabled": false,
      "settings": {"title": "..."},
      "blocks": {"slide_1": {"type": "slide@1", "settings": {}}},
      "blockOrder": ["slide_1"]
    }
  },
  "order": ["hero_1"]
}
```

`settings` is the document's own settings (a page's SEO fields), optional,
declared per kind of document by the catalogue's `documentKinds`
(`{page|template|theme|group: {schema, translatable, refs?}}`, inside the
catalogue `hash`). A kind the catalogue gives no root takes no `settings`
(`document_settings_not_allowed`). The overlay addresses them with the
reserved node id `@document` (`i18n[locale]["@document"]["/seoTitle"]`), and the
public read answers each document's `settings` already localised.

Ids match `[A-Za-z0-9_-]{1,64}` and are unique across the whole document
(the overlay addresses nodes by id). `order` and `blockOrder` list every
child exactly once.

## The catalogue is the storefront's

The storefront declares each block once and serves the result per store:
`GET {NUXT_INTERNAL_BASE_URL}/api/builder/catalogue?schema=<schema>` answers
`{hash, blocks: [...]}` with `ETag: "<hash>"` (`builder/storefront.py`; the
base URL helper and `StorefrontUnavailable` are `core/storefront.py`, shared
with `texts`). A process keeps its copy and revalidates with `If-None-Match`
every `BUILDER_CATALOGUE_REVALIDATE_SECONDS`. **When the storefront does not
answer, nothing is saved** (`503 storefront_unavailable`): the same rule as
the texts catalogue, whether or not a copy is held, so a save is never
checked against a build the storefront may no longer serve.

Each block carries `type`, `version`, `accepts`, `schema` (a closed JSON
Schema, draft 2020-12, of its `settings`), `translatable` and `refs`
(pointers within `settings`; `*` stands for every element of a list),
`limits` (`maxBlocks`, `maxPerTemplate`), `children` (`allowed`,
`maxDepth`), `kinds` (`section`, `block`), optional `groups`, `templates`,
`feature` and `deprecated`. A setting whose schema says
`contentMediaType: "text/html"` is rich text. `linkRefs` (same pointer
syntax) names the link settings, see "References, not copies"; a link pointer
cannot equal, lie under or hold a `translatable` pointer (`*` counts).
`linkRefs` is optional ONLY so that Django can deploy before the storefront
that emits it on every block (`[]` where a block has no link); a later change
makes it required, and until then a catalogue that omits it silently indexes
no link. Check the key against the real catalogue after a storefront
deployment (step 1 of the run sheet).

## What a save checks

`builder/validation.py` collects every problem as `{path, code, message}`,
`path` being a JSON pointer into what was sent. Codes are stable:

- shape: `invalid_document`, `order_mismatch`, `invalid_node_id`,
  `duplicate_node_id`, `invalid_key`, `unknown_template` (a `template:<name>` the catalogue's `templates` does not list), `document_too_large`,
  `too_many_sections`, `too_many_blocks`, `max_depth_exceeded`;
- the catalogue: `invalid_block_type`, `unknown_block_type`,
  `unknown_block_version`, `block_version_outdated`, `not_a_section`,
  `not_a_block`, `child_not_allowed`, `block_not_allowed_here`,
  `block_limit_exceeded`, `invalid_settings` (the schema, at the setting's
  own pointer);
- this store: `feature_disabled` / `unknown_feature` (`block.feature` is a
  name in `tenant/features.STORE_FEATURES`), `invalid_reference`,
  `reference_not_found` (publish only), `unsupported_markup` (rich text the
  sanitiser would lose; the rest is stored sanitised);
- translations: `unknown_locale`, `unknown_node`, `untranslatable_setting`,
  `unknown_setting`, `invalid_i18n`.

Placement: a top-level section of a `group:<name>` document must name that
group in `groups`; the theme takes sections naming `theme`; in a
`template:<name>` or `page:` document a block with `templates` must list the
template (a page is the template `page`), and a block with neither lists is
allowed anywhere except a group.

Only the **current** version of a block is accepted on a write: the catalogue
carries one schema per block. An older version is `block_version_outdated` and a newer one
`unknown_block_version`, whatever `accepts` says (the storefront may serve
`accepts` as the current version only). Old published versions keep working
because the storefront upgrades them when it renders, and a restored old
draft is accepted as a draft; the editor upgrades it, and the save and the
publish validate it.

Limits come from settings, never from code paths:
`BUILDER_MAX_SECTIONS_PER_DOCUMENT`, `BUILDER_MAX_BLOCKS_PER_NODE`,
`BUILDER_MAX_DEPTH`, `BUILDER_MAX_DOCUMENT_BYTES`, `BUILDER_MAX_PAGES`,
`BUILDER_UNRELEASED_CHECKPOINTS_KEPT`. A block's own `limits` can only
tighten them.

## Translations

`i18n[locale][node id][pointer] = value`, where `pointer` is a JSON pointer
within the node's `settings` that the block's `translatable` names
(`/title`, `/items/0/label`). A pointer may name a whole array or object
setting (`/bullets`): the overlay value then replaces it whole and is checked
against its sub-schema. Structure is never per language. The default
language is never in the overlay. At render a missing entry falls back to
the default content. An overlay value is validated by running the node's
schema over the merged settings, and reported at the overlay's pointer.

## References, not copies

Settings the catalogue marks as `refs` hold ids: `product` and `category`
(active, numeric id), `page` (a page key: the store's `page:<key>` document or the
`template:<key>` of a fixed page type, draft or released), `contentPage` (a
ContentPage slug, in any state), `blogCategory` (a blog category id), `menu`
(a navigation slot), `media` (a `MediaAsset` id), `blogPost` (a published post's
id). A draft may point at something
that no longer exists; **publish refuses it** (`reference_not_found`) with
the pointer. After a publish a target can still go away; the storefront
renders the block empty. `references.used_by(kind, target)` answers where a
thing is used.

**Links.** A setting listed in the block's `linkRefs` holds one of `{href}` (an
external address), `{route}` (a fixed route) or `{ref: {kind, id}}`, and the
catalogue's schema checks the first two. Only `{ref}` points at something of
ours, and its kind is in the data, so a static `refs` entry cannot carry it:
for each `{ref}` found at a `linkRefs` pointer, validation checks `id` has the
shape of `kind` (an unknown kind is `invalid_reference`) and records
`(kind, id, the link's own pointer)` with the other references. The version's
`refs`, the `BuilderReference` index, the publish check (`reference_not_found`)
and "where is it used" then treat it like any other reference. A link is not
translatable: it resolves to the path of the viewer's language, so an overlay
value on it is `untranslatable_setting`.

## Writes

`builder/services.py` is the only writer (permission first, then the
catalogue, then the write):

| Function | What |
|---|---|
| `save_draft` | `If-Match` revision (`0` creates); a stale one is `409 revision_conflict` with `currentRevision`, `updatedBy`, `updatedAt`; saving what is stored changes nothing |
| `create_checkpoint` | A named save; the same content is the same version; older unreleased checkpoints beyond the setting are pruned |
| `restore_version` | Back as the draft (revision + 1), not validated |
| `discard_draft` | The draft becomes the published version |
| `publish` | Validates every named draft (references included), reports every problem with its `document`, creates the versions, one release, one transaction, per-store advisory lock for the release number; `nothing_to_publish` when nothing changed |
| `rollback` | A new release with an older release's map |
| `delete_page` | A released page leaves the store with a new release; a page another released document links to is `page_in_use` |

## What a release moves

`BuilderRelease` is `invalidated_by` of the `builder` surface
(`core/cache/surfaces.py`: the storefront's `builderPage` handler, every
cached route, `invalidates_tenant_config=True`, so the Cloudflare edge purge
follows) and `tenant/signals.py: bump_generation_on_builder_release` moves
`Tenant.cache_generation` in the release's own transaction. A draft save or
a checkpoint purges nothing: no public read touches a draft.

## The API

All under `/api/v1/builder/`. Bodies are camelCase, **except a document's
own content** (`draft`, `i18n`, `data`, `theme`, `groups`, `page`,
`versions`): its keys are the storefront's and travel untouched
(`builder/rendering.py`).

Staff (`StoreStaffModelPermissions`):

| Route | |
|---|---|
| `GET documents` (`?kind=`), `GET documents/<key>` | List / draft + `draftRevision` + `ETag` |
| `PUT documents/<key>/draft` | Autosave, `If-Match` required (`428 precondition_required`) |
| `DELETE documents/<key>` | A page, `If-Match` required |
| `POST documents/<key>/discard`, `.../checkpoint` | |
| `GET documents/<key>/versions`, `.../versions/<n>`, `POST .../versions/<n>/restore` | |
| `POST publish` `{documents: [key], note}`, `POST rollback` `{release, note}`, `GET releases` | |

Roles: `builder` is in `STORE_SETTINGS_APP_LABELS` and
`STAFF_READ_ONLY_APP_LABELS`: ADMIN and OWNER edit and publish, STAFF may
only look.

Public: `GET published/<page_key>?locale=` (AllowAny) answers
`{release, locale, theme, groups: {name: doc}, page: doc}` from the current
release, resolved for the locale (overlay merged, disabled nodes dropped
with their children, `disabled` removed); `page` is the page of that key,
else the template of that name; `404 page_not_published` otherwise. It
never reads a draft and never calls the storefront. The resolved page is
cached under `builder:<schema>:r<release id>:<page_key>:<locale>`: immutable
per release, never purged. The key holds the release's PRIMARY KEY, not its
number: numbers restart at 1 after `builder_reset`, a key that came back would
serve the old release's content, and an id never does. The current release id
is kept under the store's cache generation, so a warm read touches neither the
database nor anything else.

`GET published?locale=` (AllowAny) is the page-less read: what every page needs
and none owns. `{release, locale, theme, groups, pages}`: the theme and groups
resolved like the page read, and `pages` listing every `page:` and `template:`
document of the current release as `{key, kind, updatedAt}` (`key` the
suffix, `updatedAt` when its version was made, for the sitemap's `lastmod`).
A store with no release answers `{release: 0, theme: null, groups: {}, pages:
[]}`, not 404. Cached under `builder:<schema>:r<release id>:chrome:<locale>`,
immutable per release; the `builder` surface purges the storefront's
`builderPage` and `builderChrome` handlers.

`GET link-targets?product=1,2&category=3&blogPost=4&blogCategory=5`
(AllowAny) is what the storefront resolves link references with:
`{kind: {id: slug}}` for the kinds asked about, one query per kind. An id
that is missing, inactive or unpublished is absent (the rules a publish
checks references with: active product and category, published post, any
blog category). Slugs are the same in every language. At most
`BUILDER_LINK_TARGETS_MAX_IDS` ids per kind; an id that is not a positive
integer, or too many, is `400 invalid_link_targets` naming the `param`. The
query keys are camelCase on the wire (the camel-case middleware underscores
them before the view reads them).

## The media library

`MediaAsset` (`builder/models.py`) is an image in the store's library; an
image setting is a `media` reference to its id, never a URL. The storefront
gets what it needs to render it well: the size a browser shows (EXIF
orientation applied, so it can reserve space and size `srcset`), a focal
point (`focalX`, `focalY`, 0 to 1) to crop around, the alt text per
language, and `path` (`media/<schema>/uploads/builder/<name>`, what
media-stream serves). The file is in the store's media directory
(`tenant/storage.py`).

`alt` is a parler translation, unlike a block's settings (a JSON overlay):
the row stands alone and its text is one plain field, while a block's
structure must not vary per language.

`builder/media_services.py` is the only writer. Only raster images
(`BUILDER_MEDIA_FORMATS`; an SVG can carry script and has no size to
reserve), at most `BUILDER_MEDIA_MAX_BYTES` and `BUILDER_MEDIA_MAX_PIXELS`
(`file_too_large`, `unsupported_image_type`, `image_too_large`,
`invalid_image`, `invalid_focal_point`, `unknown_locale`, `invalid_alt`).
The file never changes; a new image is a new asset.

A used asset cannot be deleted silently: the reference index of the current
release names every document that uses it, and a delete answers
`409 media_in_use` with `usedBy`. A draft that uses it is not in the index,
so deleting the asset leaves that draft's publish refused
(`reference_not_found`). Changing a used asset's alt text or focal point
moves the cache generation and the `builder` surface (a release is immutable
and the media is resolved at render, so the release cannot carry it);
changing an unused one purges nothing.

Staff (`StoreStaffModelPermissions`, so ADMIN and OWNER write, STAFF look):

| Route | |
|---|---|
| `GET media` (`?search=`, `?ordering=createdAt\|sizeBytes`) | The library, newest first, each with `usedBy` |
| `POST media` | Multipart: `file`, optional `focalX`, `focalY`, `alt` (a JSON object, language to text) |
| `GET/PATCH/DELETE media/<id>` | `PATCH` changes `focalX`, `focalY` and the alt of the languages named |

Public: `GET media/resolve?ids=1,2&locale=` (AllowAny) answers
`{results: [{id, path, width, height, mime, focalX, focalY, alt}]}` with the
alt in the asked language, else the default one. **Only assets the current
release uses are answered** (ids are sequential, and an image nobody
published is not the public's to list); any other id is left out and the
storefront shows that block empty.

## Signing in to the editor

The editor runs on the store's own host, where nobody is signed in as staff
(staff are public-schema identities; a storefront session is a customer's).
`builder/staff_session.py`:

1. Store admin, **Content -> Edit storefront** (`admin:edit-storefront`,
   `builder/admin_views.py`; only for a holder of
   `builder.change_builderdocument`, so ADMIN and OWNER; `never_cache`).
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

- **Single use.** The ticket is minted and spent by `core/tickets.py`, the
  helper the WebSocket ticket uses too: Redis `GETDEL`, one round trip, so of
  two requests with the same ticket one wins. Tickets live under the `strict:`
  cache namespace, so an unreachable Redis raises instead of failing open.
  Intercepting it after use gains nothing, and unused it dies in a minute.
- **Checked when spent, not when minted.** The person must still be an
  active staff account with the right to change this store's pages (the role
  backend, through the provenance stamp), so a role lost in between is
  `invalid_ticket`.
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
  deletes the account's staff tokens (`admin/password_reset.py`); logout
  deletes it.
- **A ticket is in a URL**, so it can reach a log or a history entry; that is
  why it lives a minute and is single use. The storefront should trade it at
  once and redirect without it.
- The sign-in route has its own throttle scope (`builder_session`): its one
  caller is our storefront, for every store, so the per-person budget of the
  platform login does not fit; a ticket is 256 random bits, so the limit
  bounds load, not guessing.

## Scheduled publishing

`builder/scheduling.py`, `builder/tasks.py`, `BuilderScheduledPublish`. A
publish can wait for a time:

- `POST schedules` `{documents, scheduledFor, note}` checks exactly what a
  publish checks (every draft valid, references existing, something to
  change) **now**, and **freezes** the drafts as versions held by the
  schedule. What goes live is what was checked, whatever is edited meanwhile.
  `scheduledFor` is an ISO 8601 time; one without an offset is read in the
  store's timezone (`settings.TIME_ZONE`, Europe/Athens). Refused:
  `schedule_in_the_past`, `schedule_too_far` (`BUILDER_SCHEDULE_MAX_DAYS`),
  `invalid_schedule_time`, `too_many_schedules`
  (`BUILDER_MAX_PENDING_SCHEDULES`), `nothing_to_publish`. Frozen versions
  are protected from checkpoint retention while the schedule is pending.
- `GET schedules` (`?status=pending|published|cancelled|failed`),
  `GET/PATCH/DELETE schedules/<id>`: `PATCH {scheduledFor}` moves it,
  `DELETE` cancels it (it stays listed as `cancelled`); once it has run or
  been cancelled, `409 schedule_not_pending`.
- The sweep `builder.tasks.run_due_publishes` runs every minute per store
  (`tenant.tasks.fanout_run_due_builder_publishes`, beat entry
  `run-due-builder-publishes`). Each due schedule is one transaction under
  the per-store release lock with `select_for_update`; the status change is
  inside it, so a retry, an overlapping sweep or a second worker finds it
  spent, and a cancel racing the sweep wins or loses cleanly.
- At execution the references are checked again and the documents must still
  exist. A schedule that can no longer be released (`reference_not_found`,
  `document_gone`, `nothing_to_publish`: something else already released
  those versions) is `failed` with the problems in `failure`, never half
  done. Otherwise it is the ordinary release (made by who scheduled it): the
  cache generation and the `builder` surface move at that moment.
- **A failure reaches a person.** When the sweep marks a schedule `failed`,
  after that commit it sends the store-staff email `ops.builder_schedule_failed`
  (`builder/emails.py`, area `ops`, switchable on the store's Emails page like
  the other `ops.*` alerts; Greek and English) naming the pages, the scheduled
  time (store timezone) and the problems in plain words. The problem sentences
  are re-read from the stored code and params in the language of the email
  (`errors.message_for`), not the language the sweep ran in. A mail that cannot
  go out never undoes the failure. The store dashboard shows a danger alert to
  anyone who may publish while a failed schedule is unacknowledged;
  `POST schedules/<id>/acknowledge` or the **Acknowledge the failure** action of
  Content, Page builder, Scheduled publishes (read-only admin) takes it away.

## Importing the legacy layouts

`page_config` (`PageLayout` + `PageSection`, `NavigationMenu`) is what the
builder replaces. `manage.py builder_import_legacy [--tenant S | --all-tenants]
[--dry-run] [--json]` (`builder/importing.py`) turns a store's layouts and
menus into documents and releases them once. The conversion is **one
implementation, the storefront's** `fromLegacy` (its blocks know their own
settings); Django never converts:

- `builder/legacy.py` describes the legacy data as the converter's request
  (`kind` template or group, `key`, `legacy`: a layout's `sections` with their
  uuid, `type`, `title`, `isVisible`, stored snake_case `props` and `i18n`, and
  its SEO per language; a menu's columns and links with the label each would
  show in every language of the store, and the target). A layout whose page
  type is one of the catalogue's top-level `templates` (the storefront's fixed
  page types: home, about, ...; read from the catalogue, never copied) is
  `template:<page_type>`, any other is the custom page `page:<page_type>`; a
  menu slot is `group:<slot>`.
- `builder/convert.py` calls `POST {storefront}/api/builder/convert-legacy`
  with the secret the cache purge uses (`NUXT_CACHE_PURGE_TOKEN`,
  header `X-Cache-Purge-Token`) and `defaultLocale`. `200 {document, i18n,
  warnings}`; `422` is a refusal about the data (unknown block type, props its
  schema refuses), anything else is the storefront being unavailable.
- Each result is validated like any other document (`builder.validation`).
  Feature gates are not applied to the import (a block whose feature is off
  renders nothing, as before; they apply on every later save).
- **All or nothing per store**, in one transaction: the documents (a layout
  that is not published stays a draft, a menu is live), one version for each
  released one, and ONE release (number 1, no staff, note "Imported from page
  layouts"). A problem anywhere, including a reference to nothing, writes
  nothing. A store that already has builder data is refused. `--dry-run` does
  everything and rolls it back; the `--json` report also gives each document's
  `counts` (`sections` and `blocks` at every depth). The legacy tables are not
  touched.
- The storefront's golden fixtures (`{request, response}` per section type,
  and the menus) are copied under `tests/fixtures/builder/legacy/` and
  replayed by `tests/integration/builder/test_converter_fixtures.py`.
- `manage.py builder_export_legacy --all-tenants --out DIR` writes every
  request as `DIR/<schema>/<key>.json`: the corpus for the storefront's tests.

## Run sheet: importing a live environment

Every command is `kubectl --context grooveshop -n <namespace> exec
deploy/backend -- /home/app/.venv/bin/python manage.py ...` (`grooveshop` is
production, `grooveshop-staging` staging; from Git Bash prefix
`MSYS_NO_PATHCONV=1`, or the path is rewritten). The import only writes builder
tables: `page_config` and every storefront read stay as they are, so nothing
changes for a visitor until the storefront switch.

1. **Before.** The backend carries the builder import and the storefront
   serves the strict converter (`POST /api/builder/convert-legacy` answers; the
   storefront image is the one the staging run used). Its catalogue declares
   `linkRefs`: `fetch_catalogue(<schema>).get("hero_carousel").link_refs` is not
   empty in the backend's shell, since an absent key would let a raw link
   through unindexed.
2. **Pre-check, on that environment's data.**
   `builder_import_legacy --all-tenants --dry-run --json`. (On a store that
   already has builder data the import refuses first, so a RE-import is checked
   by resetting and dry-run importing in one transaction that is rolled back.) Every store must
   show `problems: []` and `applied: false`. Read each `warnings` entry
   (`dropped_title` is expected, anything else is a decision). A store
   reporting `already_imported` has builder data already: stop and look at it.
3. **Import.** The same command without `--dry-run`. One transaction per
   store; a store with a problem is skipped and reported, nothing of it is
   written, and the command exits non-zero. Each store ends at release 1,
   note "Imported from page layouts", no staff.
4. **Verify (read only).** Per store: the release is 1; each document key and
   kind is the dry run's; every released document has one version (number 1)
   and `published_revision` 1; a draft layout has no version and is not in
   the release. Then `GET /api/v1/builder/published/home?locale=el` and
   `?locale=en` answer 200 with the layout's sections, and the English
   overlay is there where the legacy layout had English text. A second
   `--dry-run` answers `already_imported` for every store.
   **Links.** Every link setting of a released document (the catalogue's
   `linkRefs` pointers) is `{ref}`, `{route}` or `{href}`, never a bare string,
   and each `{ref}` has its `BuilderReference` row at the link's own pointer
   (a product link is `kind=product`, a page or content-page link `page` or
   `contentPage`). Through the storefront, the page renders the target's
   current path: a product link is `/products/<id>/<slug>`. A dangling link
   would have stopped the dry run with `reference_not_found`.
   **Caches.** An import or a reset moves the cache generation and purges the
   `builder` surface after commit, like a publish; look for
   `Cache auto-invalidated surfaces=['builder'] ... failed=[]` per store in the
   worker's log. A reset needs no cache key deleted: the reads are keyed by
   release id, which a new release never shares with a deleted one.
5. **Rollback, before the storefront reads the builder.**
   `builder_reset --tenant <schema>` reports what it would delete;
   `--confirm` deletes it (documents, versions, the release, the reference
   index). It refuses, and says why,
   unless the data is exactly what an import leaves: one release, no staff
   behind a release or version, no edited draft, named save, deleted document
   or schedule. It cannot see whether the storefront already reads the
   builder: that is the storefront deployment, so a reset belongs before it,
   never after. The media library and `page_config` are never touched.
   Afterwards the import can be run again.

### Importing before the storefront is promoted

The importer takes the catalogue and the converter from the storefront at
`NUXT_INTERNAL_BASE_URL` (`core/storefront.py`; both endpoints are tenant-free
and the converter is pure). A store's builder data is written with the
converter of the build that will render it, so when the build that carries a
new converter or catalogue is not yet what the store's storefront runs, and
promoting it first would blank every page until the import finished, import
BEFORE the promote against an internal instance of that build:

1. Run an instance of the exact image that will be promoted, reachable only
   inside the cluster (a ClusterIP Service, no Ingress), with the target
   environment's own frontend secret and config: the converter checks the same
   `NUXT_CACHE_PURGE_TOKEN` the importer sends, and each environment has its
   own. Declare it in GitOps like any other workload and remove it once the
   promote is done.
2. Point the one-off process at it with the environment variable, nothing in
   the code or the command line: `kubectl ... exec deploy/backend -- env
   NUXT_INTERNAL_BASE_URL=http://<internal service> /home/app/.venv/bin/python
   manage.py builder_import_legacy ...`. Only that process is redirected. The
   cache purge an import queues runs in the worker, on the worker's own
   environment, so it still reaches the real storefront.
3. Before any write, in the same process, compare what that instance serves
   with what will be live. The catalogue is a constant of the server bundle, so
   the same image gives the same `hash` (print `fetch_catalogue(<schema>).hash`
   with the override and again after the promote: they must be equal), and
   `fetch_catalogue(<schema>).get("hero_carousel").link_refs` must be non-empty.
   Stop on either.
4. Run the pre-check, the import and the verification (steps 2 to 4 above) with
   the override. Verify the storefront-side read only after the promote.

## What is copied from the storefront

The storefront owns the blocks, so three things are pinned COPIES of its
files:

| Source | Copy | Used by |
|---|---|---|
| `test/fixtures/builder/legacy/` | `tests/fixtures/builder/legacy/` | the converter fixture replay |
| `test/fixtures/builder/catalogue.json` | `tests/fixtures/builder/catalogue.json` | the seeds test: a snapshot of the block catalogue |
| `test/fixtures/builder/seeds/` | `builder/seeds/` | `builder.seeding`: application data |

`tests/fixtures/builder/storefront.lock.json` names the storefront commit and
the git id of each source (the tree id of a directory, the blob id of a file).
Only `.github/scripts/sync_storefront_sources.py` writes the copies and the
lock; `tests/unit/builder/test_storefront_sources_pin.py` fails when a copy is
not the pinned one, and the weekly `storefront-sources.yml` workflow fails when
the storefront's `main` has moved on. A refresh is: run the script, replay the
builder tests, commit.

## Default documents for a new store

`builder/seeding.py` loads `builder/seeds/*.json` (each a converter fixture:
the document is `response.document`, its translations `response.i18n`, the key
`request.key`). Provisioning (`tenant.provisioning`, step "page builder")
stores each as a document whose draft equals its published content, one
version, and ONE release (number 1) holding them all; today that is
`template:home`, 11 sections with fixed ids. It calls no service, so a
storefront outage cannot leave a store without its homepage, and a store that
already has builder data is left alone. What stands in for validation is
`tests/unit/builder/test_seeds.py`, which validates every seed against the
catalogue snapshot and checks the values `seeding` derives (block types,
references) equal what validation derives.

## Not here yet

The `builder_block_usage` command that will gate removing a block type is its
own change.
