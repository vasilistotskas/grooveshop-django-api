# Page builder: writes, releases, the API, previews and scheduled publishing

## Writes

`src/builder/services.py` is the only writer (permission first, then the
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
(`src/builder/cache_surfaces.py`: the storefront's `builderPage` and
`builderChrome` handlers, its cached builder functions, every cached route, `invalidates_tenant_config=True`, so the Cloudflare edge purge
follows) and `src/tenant/signals.py: bump_generation_on_builder_release` moves
`Tenant.cache_generation` in the release's own transaction. A draft save or
a checkpoint purges nothing: no public read touches a draft.

## The API

All under `/api/v1/builder/`. Bodies are camelCase, **except a document's
own content** (`draft`, `i18n`, `data`, `theme`, `groups`, `page`,
`versions`): its keys are the storefront's and travel untouched
(`src/builder/rendering.py`).

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


### Every refusal is declared

An operation declares each status it can answer, with its body, so the
storefront needs no schema of its own for a refusal:

- `400`: `ApiValidationError`, `ApiError` and, where it checks a document, a
  schedule time, a preview or a presence list, `BuilderDocumentInvalidError`
  (`errors: PublishProblem[]`).
- `404` (`not_found`, `preview_link_expired`, ...), `503`
  (`storefront_unavailable`) and `428` (`precondition_required`): the plain
  `{detail, code}`.
- `409`: `BuilderRevisionConflict {code: revision_conflict, currentRevision,
  updatedBy, updatedAt}` on every operation that takes `If-Match`;
  `BuilderMediaInUse {code: media_in_use, usedBy}` on deleting an asset;
  otherwise the plain body (`schedule_not_pending`, `schedule_not_failed`).

`tests/response_contract.py` holds every request a test makes to it: under
`/api/v1/builder/` a path no operation fits, an answer whose status its
operation does not declare (other than 401/403, answered before any view),
or a JSON body that does not satisfy the declared schema fails the test
that provoked it. A declared answer that is not JSON is not judged by its
body. So a new operation or a new refusal that a test reaches cannot go
undeclared.

## The live preview

`POST builder/preview/resolve` (`src/builder/services.py: preview`,
`src/builder/published.py: preview`) answers what the public read would answer if
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
  validation.

## The editor's link pickers

Staff routes (`StoreStaffModelPermissions`, so every role looks), in
`src/builder/link_picker.py`. The existence rule is `references.VISIBLE`, the one
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

`src/builder/preview_links.py`, `BuilderPreviewLink`. A store shows unpublished work
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
  deletes the table (`src/devtools/anonymise/rules.py`): a link is a credential.

## Presence

`POST builder/presence {documents: [key]}` is the editor's heartbeat and
`DELETE builder/presence` its sign-out (`src/builder/presence.py`). The heartbeat
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

`GET features` (`src/builder/features.py`, any store role) answers `{features:
{<name>: {on, turnOnUrl}}}` for every name of `TenantConfig.features`
(`src/tenant/features.py: STORE_FEATURES`), so the editor can mark a block whose
feature is off (`feature_disabled`) and offer "Turn on <feature>" without
copying the plan/setting split or guessing admin addresses. It reads the table
and the request-scoped state the admin uses (`admin.features.StoreFeatures`),
so it cannot disagree with the 404 gates or the sidebar.

- `on`: the plan flag and every setting the feature needs are on.
- `turnOnUrl`: the store admin's change page of the first setting that is off,
  on the store's own API host (`get_tenant_api_base_url`). Every declared
  setting has its row (`migrate` creates the missing ones, and the settings
  admin refuses to delete a declared one: `src/core/admin.py: SettingAdmin`), so
  the page exists; a name with no row, which only an undeclared setting can
  be, has no link.
  `null` when the feature is on, when the plan keeps it off (only the platform
  can change that), or when the caller's role lacks
  `extra_settings.change_setting` (`CONFIGURE_PERMISSION`: Admin and Owner hold
  it, Staff do not).
- A feature that needs several settings offers the first off one in the order
  the table lists them (the cart needs the catalogue first).

## Scheduled publishing

`src/builder/scheduling.py`, `src/builder/tasks.py`, `BuilderScheduledPublish`. A
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
  exist. A schedule that cannot be released (`reference_not_found`,
  `document_gone`, `nothing_to_publish`: something else already released
  those versions) is `failed` with the problems in `failure`, never half
  done. Otherwise it is the ordinary release (made by who scheduled it): the
  cache generation and the `builder` surface move at that moment.
- **A failure reaches a person.** When the sweep marks a schedule `failed`,
  after that commit it sends the store-staff email `ops.builder_schedule_failed`
  (`src/builder/emails.py`, area `ops`, switchable on the store's Emails page like
  the other `ops.*` alerts; Greek and English) naming the pages, the scheduled
  time (store timezone) and the problems in plain words. The problem sentences
  are re-read from the stored code and params in the language of the email
  (`errors.message_for`), not the language the sweep ran in. A mail that cannot
  go out never undoes the failure. The store dashboard shows a danger alert to
  anyone who may publish while a failed schedule is unacknowledged;
  `POST schedules/<id>/acknowledge` or the **Acknowledge the failure** action of
  Content, Page builder, Scheduled publishes (read-only admin) takes it away.
