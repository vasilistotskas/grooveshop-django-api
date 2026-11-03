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
  `size_bytes`. `kind` is `checkpoint` (a named save), `publish` or `restore` (a
  restored version; `restored_from` is the number it restores, `null` for
  every other kind).
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
syntax, required on every block and every `documentKinds` root, `[]` for
none) names the link settings, see "References, not copies"; a catalogue
that leaves the key out is refused, since it would index no link. A link
pointer cannot equal, lie under or hold a `translatable` pointer (`*`
counts). `tests/unit/builder/test_real_catalogue.py` holds the pinned real
catalogue to this: each `linkRefs` is exactly the settings its schema marks
`x-builder-link`.

## What a save checks

`builder/validation.py` collects every problem as `{path, code, message}`,
`path` being a JSON pointer into what was sent. On the wire a problem is
always `{path, code, message, document, params}` (`Issue.as_dict`, held equal
to `PublishProblemSerializer`): `document` and `params` are `null` when there
are none. A refusal that lists them is a `400 invalid_document` whose body is
`BuilderDocumentInvalidError {detail, code, errors: PublishProblem[]}`, the
third branch of the 400 `anyOf` on every operation that can raise it; a failed
schedule's `failure` is the same `PublishProblem[]`. Codes are stable:

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
ContentPage slug, in any state), `blogCategory` (a blog category id),
`media` (a `MediaAsset` id), `blogPost` (a published post's id). A menu is a
`group:<slot>` document, never a reference. A draft may point at something
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


### Typed data props (`builder/data_sources.py`)

A block can declare a setting in the catalogue's `data` map, beside `refs`
(both are read until the storefront stops serving `refs`):

- `{field: "ref", kind, onMissing}`: the setting holds one record's id (a slug
  for a page), checked like a `refs` entry;
- `{field: "query", source, onEmpty, maxLimit}`: the setting holds a descriptor,
  a hand-picked list (`{mode: "picked", ids}`) or a rule
  (`{mode: "filter", ...}`, `{mode: "roots" | "children", ...}`, `{limit}`),
  for `source` in `product`, `blogPost`, `category`, `blogCategory`,
  `promotion`.

`data_sources.check(declaration, value)` judges a value and says what it
names. A value that is not valid yields problems and nothing else:
`invalid_data_value`, `unknown_filter` (a key the list does not have) and
`too_many_ids`, each at the pointer of the offending key; a document that
asks for more than `BUILDER_DATA_MAX_QUERIES` lists gets `too_many_queries`.
The vocabulary is held equal to the storefront's by
`tests/fixtures/builder/data-cases.json`, copied and pinned like the other
sources (`tests/unit/builder/test_data_sources.py` runs every case).

A valid value yields two kinds of index row, both in `BuilderVersion.refs` and,
for the current release, `BuilderReference`:

- the records it names (`product`, `category`, `blogCategory`, `blogPost`):
  existence-checked at publish and listed by "used on N pages";
- the lists it asks for, as a query kind (`queryProduct`, `queryBlogPost`,
  `queryCategory`, `queryBlogCategory`, `queryPromotion`) whose `target` is a
  facet: `*` when no filter bounds the list, `category:<id>` or `parent:<id>`
  when one does. One row per facet: a change matches any of them, which can
  over-purge and never under-purge. A facet is not a record, so it is never
  existence-checked.

## Writes

`builder/services.py` is the only writer (permission first, then the
catalogue, then the write):

| Function | What |
|---|---|
| `save_draft` | `If-Match` revision (`0` creates); a stale one is `409 revision_conflict` with `currentRevision`, `updatedBy`, `updatedAt`; saving what is stored changes nothing |
| `create_checkpoint` | A named save; the same content is the same version; older unreleased checkpoints and restores beyond the setting are pruned |
| `restore_version` | Back as the draft (revision + 1), not validated; records a `restore` version naming the source number (nothing new when the newest version already holds that content) |
| `discard_draft` | The draft becomes the published version |
| `publish` | Validates every named draft (references included), reports every problem with its `document`, creates the versions, one release, one transaction, per-store advisory lock for the release number; `nothing_to_publish` when nothing changed |
| `rollback` | A new release with an older release's map |
| `publish_version` | A new release that repoints ONE key at one of its stored versions and leaves every other document and the draft alone; its references must exist (`reference_not_found`), `nothing_to_publish` when it is already the published one; the document counts as published only if its draft is exactly that version |
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
| `GET documents` (`?kind=theme\|group\|template\|page`, another value is `400 invalid_kind`), `GET documents/<key>` | List / draft + `draftRevision` + `ETag` |
| `PUT documents/<key>/draft` | Autosave, `If-Match` required (`428 precondition_required`) |
| `DELETE documents/<key>` | A page, `If-Match` required |
| `POST documents/<key>/discard`, `.../checkpoint` | |
| `GET documents/<key>/versions`, `.../versions/<n>`, `POST .../versions/<n>/restore` | |
| `POST documents/<key>/versions/<n>/publish` `{note}` | Release that version of that key alone (a release, not a rollback: `rolledBackFrom` is empty) |
| `POST publish` `{documents: [key], note}`, `POST rollback` `{release, note}`, `GET releases` | A release lists `versions` (`{key: version id}`) and `documents: [{key, versionNumber, changed}]`: each document it holds at which version number, and whether it differs from the release before it (the first changes everything) |
| `POST publish/check` `{documents: [key]}` | What the publish would do, writing nothing: `{ok, changed: [key], problems: [{document, path, code, message, params?}]}`. `changed` are the documents whose draft differs from the version the current release holds (by content, so editing back to the released text is not a change); `nothing_to_publish` is a problem when none is. Same permission as the publish, no lock taken; unknown document 404, no catalogue 503 |

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
number: a number can come back (a store's builder data deleted and seeded
again starts at 1) and would serve the old release's content; an id never
does. The current release id
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

## The live preview

`POST builder/preview/resolve` (`builder/services.py: preview`,
`builder/published.py: preview`) answers what the public read would answer if
the editor's **unsaved working copies** were released now, so the editor can
render a page without saving or publishing it.

```json
{"pageKey": "about", "locale": "en",
 "documents": {"page:about": {"draft": {...}, "i18n": {...}}}}
```

- The answer is `GET published/<pageKey>`'s: `{release, locale, theme, groups,
  page}`. Each copy replaces the current release's document of the same key
  (`theme`, `group:<n>`, `template:<n>`, `page:<n>`, a page before a template
  of the same name); everything not sent is the release's; a store with no
  release answers `release: 0`. A working copy has `version: 0`.
- **One code path.** `published.assemble` puts a page together for the
  release read and for the preview alike (overlay merged, disabled nodes
  dropped); the preview only supplies the documents. The test that pins it
  sends the stored drafts and compares the answer to the public read.
- Every copy is validated like a draft save (`400 invalid_document`, every
  problem with its `document`; references are not checked, as on a save; no
  copies need no catalogue). At most `BUILDER_PREVIEW_MAX_DOCUMENTS` copies
  (`too_many_documents`). A page that neither a copy nor the release has is
  `404 page_not_published`. An unknown or missing `locale` is the default one.
- Nothing is stored and nothing is cached: `Cache-Control: private,
  no-store`. Allowed to every store role (`builder.view_builderdocument`), so
  the read-only editor of a STAFF member previews too. A map of document keys
  is not camel-cased (`PreviewJSONParser`).
- Cost: a fixed handful of queries (the release, its versions) plus the
  validation; measured warm in the test client, about 8 ms for 5 sections,
  17 ms for 20 and 27 ms for 40.

## The editor's link pickers

Staff routes (`StoreStaffModelPermissions`, so every role looks), in
`builder/link_picker.py`. The existence rule is `references.VISIBLE`, the one
the publish check and `link-targets` use; the pickers add no second copy of
it. No route is built here: the storefront composes the path from the slug.

- `GET link-search?q=&kinds=&locale=` answers `{results: [{kind, id, label,
  slug, active}]}` over `product`, `category`, `blogPost`, `blogCategory`,
  `page` and `contentPage` (`kinds` a comma-separated list, all when omitted,
  an unknown one `400 invalid_link_search`). Grouped by kind in the order of
  `kinds`, each group by label, at most `BUILDER_LINK_SEARCH_LIMIT` of a kind.
  `q` matches the label (in `locale`, else the default language, else the
  slug) or the slug, and a product's id. A `q` shorter than
  `BUILDER_LINK_SEARCH_MIN_CHARS` is an empty list, not an error. Only what a
  link may point at is offered, so `active` is always true. `id` is a number
  for the four catalogue kinds and the key or slug for `page` and
  `contentPage`; a `page` result also has `source` (`page` for a store page,
  `template` for a fixed page type). A page is labelled with its SEO title in
  `locale`, else its default one, else its key; a template with its key.
- `GET link-labels?product=1,2&category=&blogPost=&blogCategory=&page=&contentPage=&locale=`
  answers `{kind: {id: {label, active}}}` for the ids asked about, so a stored
  reference shows its name: `active` is the same rule, an inactive or
  unpublished record keeps its `label` with `active: false`, and an id that
  exists nowhere is `{label: null, active: false}`. A `page` entry also has
  `source`; a key that is both a store page and a fixed page type is answered
  as the page, as the public read does. At most `BUILDER_LINK_TARGETS_MAX_IDS`
  per kind, otherwise `400 invalid_link_targets` with the `param`. The ids
  are keys of the answer and are not camel-cased (`LinkLabelsRenderer`).

Both cost one query per kind: the label is a database annotation, not a read
per row.

## Share-preview links

`builder/preview_links.py`, `BuilderPreviewLink`. A store shows unpublished work
to someone without an account:

- `POST preview-links {documents, note?}` (the change permission: ADMIN and
  OWNER) validates the current drafts like a draft save (references are not
  checked) and **freezes** them in the link, outside the version history: no
  `BuilderVersion` is made, so numbering, retention and releases are untouched.
  It answers `{token, id, documents, note, createdBy, createdAt, expiresAt}`;
  the token (`secrets.token_urlsafe(32)`, 256 random bits) is in this answer
  only, the database holds its SHA-256. A link lives
  `BUILDER_PREVIEW_LINK_TTL_SECONDS`; `BUILDER_MAX_PREVIEW_LINKS` may be open
  at once (`too_many_preview_links`) and a link takes at most
  `BUILDER_PREVIEW_MAX_DOCUMENTS` documents.
- `GET preview-links` lists the open ones (no token), `DELETE
  preview-links/<id>` revokes one at once (the change permission; STAFF may
  list).
- `GET preview/<token>/<pageKey>?locale=` (AllowAny) answers the shape of
  `published/<pageKey>`, the frozen documents standing in for the current
  release's of the same keys, through the same code as the preview
  (`published.preview`): later edits and publishes do not change what was
  shared, the rest of the page is the live release's. A token that is
  unknown, revoked or expired is one answer, `404 preview_link_expired`;
  a page neither the link nor the release has is `404 page_not_published`.
- **Not cached, not indexed**: `Cache-Control: private, no-store` and
  `X-Robots-Tag: noindex, nofollow`. The edge's storefront-pages rule already
  excludes `/api`, so this route is never stored; the storefront page that
  renders a shared preview must answer `private, no-store` too, because the
  rule caches anything else an origin marks cacheable
  (`grooveshop-infrastructure/docs/cloudflare-zone-settings.md`).
- It shows nothing the published read does not: the live release's documents
  and the shared drafts, no store data, no staff identity (the list names who
  shared a link, to staff only).
- `builder.tasks.purge_expired_preview_links` (daily, per store, suspended
  stores included: `tenant.tasks.fanout_purge_expired_builder_preview_links`)
  deletes the expired links and the drafts they hold. The staging anonymiser
  deletes the table (`devtools/anonymise/rules.py`): a link is a credential.

## Presence

`POST builder/presence {documents: [key]}` is the editor's heartbeat and
`DELETE builder/presence` its sign-out (`builder/presence.py`). The heartbeat
names the documents the editor has open (the full list: ones left out are let
go of) and answers `{documents: {key: [{id, name, since}]}}`: who ELSE sent a
heartbeat for each, oldest first, the caller left out. It is a hint, not a
lock: the draft's `If-Match` revision still decides a save.

- Kept in the cache under the active schema (a heartbeat on one store is
  invisible on another, `tests_mt/test_builder_presence_isolation.py`), never
  in the database: a record per person and document for
  `BUILDER_PRESENCE_TTL_SECONDS`, a Redis set of ids per document so a read
  never scans, and a set of what each person has open. A person whose record
  expired drops out of the answer and out of the set at the next read;
  `since` survives heartbeats and starts again after a lapse.
- A key that is not a document key is `400 invalid_document` (`invalid_key`),
  more than `BUILDER_PRESENCE_MAX_DOCUMENTS` is `too_many_documents`; a
  refused heartbeat changes nothing.
- Every store role may call it (`builder.view_builderdocument`), so the
  read-only editor of a STAFF member is seen too.

## Which features are on, and where to turn one on

`GET features` (`builder/features.py`, any store role) answers `{features:
{<name>: {on, turnOnUrl}}}` for every name of `TenantConfig.features`
(`tenant/features.py: STORE_FEATURES`), so the editor can mark a block whose
feature is off (`feature_disabled`) and offer "Turn on <feature>" without
copying the plan/setting split or guessing admin addresses. It reads the table
and the request-scoped state the admin uses (`admin.features.StoreFeatures`),
so it cannot disagree with the 404 gates or the sidebar.

- `on`: the plan flag and every setting the feature needs are on.
- `turnOnUrl`: the store admin's change page of the first setting that is off,
  on the store's own API host (`get_tenant_api_base_url`). Every declared
  setting has its row (`migrate` creates the missing ones, and the settings
  admin refuses to delete a declared one: `core/admin.py: SettingAdmin`), so
  the page exists; a name with no row, which only an undeclared setting can
  be, has no link.
  `null` when the feature is on, when the plan keeps it off (only the platform
  can change that), or when the caller's role lacks
  `extra_settings.change_setting` (`CONFIGURE_PERMISSION`: Admin and Owner hold
  it, Staff do not).
- A feature that needs several settings offers the first off one in the order
  the table lists them (the cart needs the catalogue first).

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

`builder/media_services.py` is the only writer. Only raster images (an
SVG can carry script and has no size to reserve), held to the rule every
image upload shares ([`docs/image-uploads.md`](image-uploads.md): the media
service's per-format byte caps and a pixel budget) (`file_too_large`,
`unsupported_image_type`, `image_too_large`, `invalid_image`,
`invalid_focal_point`, `unknown_locale`, `invalid_alt`). An import is cut
off at the largest of those caps while it streams.
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
| `GET media` (`?search=` on the file name and the alt text, `?ordering=createdAt\|sizeBytes`) | The library, newest first, each with `usedBy` |
| `POST media` | Multipart: `file`, optional `focalX`, `focalY`, `alt` (a JSON object, language to text). The same image again (SHA-256 of the bytes, `MediaAsset.content_hash`) is not stored twice: the answer is the asset already there (its alt text and focal point kept) with `duplicate: true` |
| `POST media/import` `{url, focalX?, focalY?, alt?}` | Bring in an image of THIS store by path (below); `duplicate: true` when it was already there |
| `GET media/staff-resolve?ids=&locale=` | Any asset of the library resolved like `media/resolve`, published or not; `private, no-store` |
| `GET/PATCH/DELETE media/<id>` | `PATCH` changes `focalX`, `focalY` and the alt of the languages named |

Public: `GET media/resolve?ids=1,2&locale=` (AllowAny) answers
`{results: [{id, path, width, height, mime, focalX, focalY, alt}]}` with the
alt in the asked language, else the default one. **Only assets the current
release uses are answered** (ids are sequential, and an image nobody
published is not the public's to list); any other id is left out and the
storefront shows that block empty.

### Importing an image the store already shows

`media_services.import_url` (`POST media/import`, ADMIN and OWNER) turns an
existing image into an asset so a block can reference it. Only a PATH of this
store is a source, never an address:

- `/img/...` (a file the storefront serves): fetched from
  `NUXT_INTERNAL_BASE_URL` with `allow_redirects=False` and the upload size
  limit while streaming; a redirect is `import_redirect`, a non-200
  `import_not_found`, an unreachable storefront `503 storefront_unavailable`.
- `media/<this store>/uploads/...` (already uploaded): read from the store's
  storage and COPIED into `uploads/builder/`, so deleting the asset never
  removes the original; another store's path is `import_other_store`, anything
  outside `uploads/` is `invalid_import_url`.
- A scheme or a host (`https://...`, `//host/...`, `file:`, `data:`) is
  `external_url_not_allowed`: the server does not fetch internet addresses on a
  staff member's behalf (an internal service could be one) and does not own them.
  A query, a fragment, a backslash, a dot segment, a space or a control
  character is `invalid_import_url`.

The bytes then go through the upload checks (`invalid_image`,
`unsupported_image_type`, `file_too_large`, `image_too_large`).

### Resolving media for a draft, a preview and a share link

The public `media/resolve` answers only what the CURRENT release uses, so an
image picked in the editor but not yet published would render empty.
`media/staff-resolve` answers any asset to staff for the editor's preview. A
share-preview link (below) has its own `GET preview-media/<token>?ids=`: it
answers only the assets the link's frozen documents reference (their `refs`
are frozen with them), so a token never opens the library; an unknown, revoked
or expired token is `404 preview_link_expired`. Both are `private, no-store`.

### Images documents still name by URL

Before the media picker a block held an image as a string (`/img/x.jpg`,
`/uploads/y.jpg`). `manage.py import_builder_media --tenant <schema>`
(`builder/media_migration.py`) brings those into the library:

- It reads every string of the drafts and of the published versions
  (`--all-versions` for every stored version), by value, never by field name:
  the catalogue does not mark those fields. An image path of the store is
  `/img/...`, `/uploads/...` or `/media/<store>/uploads/...` ending in an image
  extension; `/uploads/x` is read as `/media/<store>/uploads/x`.
- Each distinct path goes through the same import as `media/import`
  (`media_services.store_import`: the upload checks, the shrinking of an
  oversize photo, de-duplication by content), so running it again finds the
  assets it made. It prints, per image, where it is used (document, source,
  JSON pointer) and the asset it became, and `--mapping-file` writes the
  `{url: asset id}` map (a real run, one tenant).
- An absolute `http(s)` address or another store's file is listed as EXTERNAL
  and never fetched. An SVG is refused by the library (a raster only) and a
  file that cannot be read is reported `FAILED (<code>)`; the rest carry on.
- `--dry-run` reads the same files and writes nothing: no asset, no file.
- It never rewrites a document. What replaces the URL is the block's
  decision (its version upgrade), which the storefront owns.

## Signing in to the editor

The editor runs on the store's own host, where nobody is signed in as staff
(staff are public-schema identities; a storefront session is a customer's).
`builder/staff_session.py`:

1. Store admin, **Content -> Edit storefront** (`admin:edit-storefront`,
   `builder/admin_views.py`; for a holder of `builder.view_builderdocument`,
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

- **Single use.** The ticket is minted and spent by `core/tickets.py`, the
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

## What is copied from the storefront

The storefront owns the blocks, so these are pinned COPIES of its
files (the media service's image limits are copied the same way, see
[`docs/image-uploads.md`](image-uploads.md)):

| Source | Copy | Used by |
|---|---|---|
| `test/fixtures/builder/catalogue.json` | `tests/fixtures/builder/catalogue.json` | the seeds and real-catalogue tests: a snapshot of the block catalogue |
| `test/fixtures/builder/seeds/` | `builder/seeds/` | `builder.seeding`: application data |

`tests/fixtures/upstream-sources.lock.json` names each owner's commit and
the git id of each source (the tree id of a directory, the blob id of a file).
Only `.github/scripts/sync_upstream_sources.py` writes the copies and the
lock; `tests/unit/core/test_upstream_sources_pin.py` fails when a copy is
not the pinned one, and the weekly `upstream-sources.yml` workflow fails when
an owner's `main` has moved on. A refresh is: run the script, replay the
builder tests, commit.

## Default documents for a new store

`builder/seeding.py` loads `builder/seeds/*.json` (each `{key, document,
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

`devtools/demo_pages/*.json` are the demo store's pages and menus (home,
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
