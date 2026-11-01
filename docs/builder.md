# Storefront page builder (`builder/`)

A store's theme, section groups (header, footer, ...), templates and pages
are **documents**: versioned JSON that the store's staff edit in the
storefront's visual editor and publish together. Source: `builder/`
(`models.py`, `services.py`, `validation.py`, `storefront.py`,
`references.py`, `published.py`, `views.py`, `rendering.py`), the
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

Ids match `[A-Za-z0-9_-]{1,64}` and are unique across the whole document
(the overlay addresses nodes by id). `order` and `blockOrder` list every
child exactly once.

## The catalogue is the storefront's

The storefront declares each block once and serves the result per store:
`GET {NUXT_INTERNAL_BASE_URL}/api/builder/catalogue?schema=<schema>` answers
`{hash, blocks: [...]}` with `ETag: "<hash>"` (`builder/storefront.py`; the
base URL helper and `StorefrontUnavailable` are `core/storefront.py`, shared
with `texts`). A process keeps its copy and revalidates with `If-None-Match`
every `BUILDER_CATALOGUE_REVALIDATE_SECONDS`. **When no catalogue can be
fetched and none is held, nothing is saved** (`503 storefront_unavailable`).
A copy already held outlives a failed revalidation: a deploy of the
storefront is the only thing that changes it.

Each block carries `type`, `version`, `accepts`, `schema` (a closed JSON
Schema, draft 2020-12, of its `settings`), `translatable` and `refs`
(pointers within `settings`; `*` stands for every element of a list),
`limits` (`maxBlocks`, `maxPerTemplate`), `children` (`allowed`,
`maxDepth`), `kinds` (`section`, `block`), optional `groups`, `templates`,
`feature` and `deprecated`. A setting whose schema says
`contentMediaType: "text/html"` is rich text.

## What a save checks

`builder/validation.py` collects every problem as `{path, code, message}`,
`path` being a JSON pointer into what was sent. Codes are stable:

- shape: `invalid_document`, `order_mismatch`, `invalid_node_id`,
  `duplicate_node_id`, `invalid_key`, `document_too_large`,
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
(`/title`, `/items/0/label`). Structure is never per language. The default
language is never in the overlay. At render a missing entry falls back to
the default content. An overlay value is validated by running the node's
schema over the merged settings, and reported at the overlay's pointer.

## References, not copies

Settings the catalogue marks as `refs` hold ids: `product` and `category`
(active, numeric id), `page` (a published content page slug, or a page
document of the store), `menu` (a navigation slot), `media` (until
`MediaAsset` exists nothing satisfies it). A draft may point at something
that no longer exists; **publish refuses it** (`reference_not_found`) with
the pointer. After a publish a target can still go away; the storefront
renders the block empty. `references.used_by(kind, target)` answers where a
thing is used.

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
cached under `builder:<schema>:r<release>:<page_key>:<locale>`: immutable
per release, never purged. The current release number is kept under the
store's cache generation, so a warm read touches neither the database nor
anything else.

## Not here yet

Scheduled publishing, the media library (`MediaAsset`, `ref:media`), the
storefront staff session, and the `builder_block_usage` command that will
gate removing a block type: each is its own change.
