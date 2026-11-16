# Page builder: the media library

## The media library

`MediaAsset` (`src/builder/models.py`) is an image in the store's library; an
image setting is a `media` reference to its id, never a URL. The storefront
gets what it needs to render it well: the size a browser shows (EXIF
orientation applied, so it can reserve space and size `srcset`), a focal
point (`focalX`, `focalY`, 0 to 1) to crop around, the alt text per
language, and `path` (`media/<schema>/uploads/builder/<name>`, what
media-stream serves). The file is in the store's media directory
(`src/tenant/storage.py`).

`alt` is a parler translation, unlike a block's settings (a JSON overlay):
the row stands alone and its text is one plain field, while a block's
structure must not vary per language.

`src/builder/media_services.py` is the only writer. Only raster images (an
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
share-preview link ([`publishing.md`](publishing.md)) has its own `GET preview-media/<token>?ids=`: it
answers only the assets the link's frozen documents reference (their `refs`
are frozen with them), so a token never opens the library; an unknown, revoked
or expired token is `404 preview_link_expired`. Both are `private, no-store`.

### Images documents still name by URL

Before the media picker a block held an image as a string (`/img/x.jpg`,
`/uploads/y.jpg`). `manage.py import_builder_media --tenant <schema>`
(`src/builder/media_migration.py`) brings those into the library:

- It reads every string of the drafts and of the published versions
  (`--all-versions` for every stored version), by value, never by field name:
  the catalogue does not mark those fields. An image path of the store is
  `/img/...`, `/uploads/...` or `/media/<store>/uploads/...`, the last also as
  Django writes it with no leading slash, ending in an image extension;
  `/uploads/x` is read as `/media/<store>/uploads/x`.
- An absolute address on one of the store's own hosts is the path it names
  (`media_migration.own_hosts`), which is what a stored URL looks like after
  a store changed its domain or wrote its media host in full. The hosts are
  read, never written down: the store's `TenantDomain` rows, where any of the
  paths above is the store's; and the hosts of `MEDIA_STREAM_BASE_URL` and
  `STATIC_BASE_URL`, which every store shares and where only
  `/media/<store>/uploads/...` is this store's. The file is then read by its
  path from where the store keeps it; the address itself is never fetched. A
  domain the store no longer has is not its host any more.
- Each distinct path goes through the same import as `media/import`
  (`media_services.store_import`: the upload checks, the shrinking of an
  oversize photo, de-duplication by content), so running it again finds the
  assets it made. It prints, per image, where it is used (document, source,
  JSON pointer) and the asset it became, and `--mapping-file` writes the
  `{url: asset id}` map (a real run, one tenant).
- Any other absolute `http(s)` address, and another store's file, is listed
  as EXTERNAL and never fetched. An SVG is refused by the library (a raster only) and a
  file that cannot be read is reported `FAILED (<code>)`; the rest carry on.
- `--dry-run` reads the same files and writes nothing: no asset, no file.
- It never rewrites a document.

`manage.py migrate_builder_media --tenant <schema>` does, once the storefront
serves the blocks' new settings and the images are imported. The storefront
declares each block's old image settings (`legacyImages`, vendored as
`src/builder/legacy_image_fields.json`). Every draft and stored version gets the
asset in place of the URL and loses the URL and its alt, in the settings and in
the overlay, through `src/builder/document_rewrite.py`: each document is validated
against the store's catalogue, and a version keeps its number. It ends with
a new release of the same versions, because the public read is cached per
release: that is what brings the rewrite to visitors.

- **Alt text belongs to the image.** A use's alt goes onto the asset, per
  language, only where the asset has none. Only the drafts and the current
  release decide it; what an older version says is text the store has replaced.
  What the image already says wins, and each use that gives way to it is
  printed to read. Uses that disagree, of an image with no alt, stop the store.
- **It imports nothing.** An image of the store that is not in the library yet
  stops the store: run `import_builder_media --all-versions` first. That
  import is the only automatic fix, and its limits are the library's: a raster
  file that exists in the store's own uploads or under the storefront's
  `/img/`, an oversize photo shrunk. It never takes an address outside the
  store (nothing is fetched for a staff member's sake), another store's file,
  an SVG, a path with a query string, or a file that is gone.
- **What stops a store.** A value with no library image, alt texts that
  disagree, an empty list entry that cannot keep its place (below), a document
  that does not validate, and something that changed while the command read
  the store (next point). Each is printed with every document, version, block
  and setting that holds it, and the command ends with exit 1. One store is
  one transaction: nothing of a stopped store is written, the other stores are.
- **What is written is planned from locked rows.** Finding the asset of each
  URL reads every image, too long to hold a store's documents for, so that is
  done first with no lock. Then, in one transaction, the store is locked
  (below: release sequence, documents, images), read again, and everything
  that is written comes from that second read: which settings there are, what
  alt each use gives, what alt each image already has. So a staff change
  between the two reads is seen, or stops the store, and is never overwritten:
  an alt an editor saved for the same URL is the one that moves; an alt staff
  wrote on the library image wins, as any existing alt does; a URL saved in
  between, an image deleted in between and a page created in between each stop
  the store with `run it again`.
- **An image a release names is not deleted under it.** Deleting an asset
  locks its row and then reads who uses it (`media_services.delete_asset`),
  so a delete that meets a run waits for it and is then refused with
  `media_in_use`, like any delete of an image a published page shows: the
  release the run made rebuilt the reference index it reads.
- **A person decides what the import cannot fix**, one by one, in a
  JSON file passed as `--decisions`
  (`builder.media_migration.read_decisions`):

  ```json
  {"webside": {
    "images": {"https://example.org/a.png": 12, "/img/old-logo.svg": "empty"},
    "alt": {"12": {"el": "Ο πάγκος", "en": ""}},
    "slots": {
      "template:home draft: /sections/hero/settings/mobileImages/1": 31,
      "template:home version 3: /sections/hero/settings/mobileImages/1": "remove"
    },
    "left": ["template:home version 2"]}}
  ```

  A store is named by its schema. Under `images`, a value exactly as the
  report prints it becomes the library image of that id, or no image
  (`"empty"`: the setting is left unset and its alt goes with it). Under
  `alt`, an image whose uses disagree gets the text stated, per language; an
  empty text leaves it without one. Under `slots`, an empty entry of a list
  (next point) gets a library image, or is taken out with its position. Under
  `left`, an old version that cannot be rewritten is left as it is (below). There
  is no switch that ignores problems and no default: what has no decision
  still stops the store. Each decision used is printed, and what was decided
  for a store is kept with its run (`BuilderRewriteRun.decisions`), which
  `builder_rewrite_backups` lists.
- **The file is held to its word.** A name in it that is no store (`websid`)
  stops the command before anything is read. A decision nothing used stops its
  store like any other problem: a value no document holds, an alt for an image
  that has its own or whose uses agree, a slot that is no empty entry beside
  an image. So a file with a typo, or one written for another day, cannot pass
  unnoticed.
- **One file, several stores, given again as it is.** Each store is its own
  transaction, so with `--all-tenants` one store can be moved and committed
  while another stops. Fix the stopped store's section and give the same file
  again: nothing has to be taken out. A decision that settles nothing now but
  that an earlier run of that store applied, exactly as the file states it,
  is printed as `decision already applied by an earlier run` and is no
  problem; the evidence is what that run recorded
  (`BuilderRewriteRun.decisions`). An alt decision whose text the image
  already has is `already in place`. Only a decision that is neither usable
  now nor recorded, or that the file now states differently, stops the store.
  A run that was put back recorded nothing any more. With `--tenant`, the
  sections of other stores are skipped and said.
- **A version that no longer validates.** What is stored is what
  `validate_document` returns against the storefront's catalogue of *today*:
  that is where a rewritten document's references and hash come from. A
  version was valid when it was made and nothing validates it again
  (`publish_version` and `rollback` say so), so an old one can fail today for
  a reason that has nothing to do with images: a block the storefront has
  since dropped or changed. Nobody can edit a version and no decision makes
  one valid, so what happens depends on who reads it.
  - A version **a release holds, or a publish is waiting to release**, is what
    visitors read. It has to be moved, and it stops the store. The line names
    the issue, its place and the block (`unknown_block_type at
    /sections/p/type (promo_strip@1); it is published, so it has to be
    moved`). A block left at an older version is moved by
    `persist_block_upgrades`, to be run first; anything else is fixed by
    publishing a page that validates.
  - A version **in no release** is read again only if someone rolls back to
    it. Where it did not validate before the run either, it can only be left
    as it is: not rewritten, no backup. That has a consequence, so it is not a
    default: the store stops until the version is named under `left` in the
    decisions file, as the report names it. From then on each run prints
    `decision: <version> is left as it is (<why>)` and exits 0, and the run
    keeps it (`BuilderRewriteRun.decisions`), also when nothing else was
    moved, so `builder_rewrite_backups` shows it later. The consequence: a
    release rolled back to that version shows no image where it names one by
    URL, once the storefront stops reading the old settings.
  - A version in no release that validated before the run and does not after
    it is the run's own doing. It always stops the store and cannot be left.
- **A deleted page.** Nobody can open it in the editor, and one brought back
  starts with an empty draft (`services._create_document`), so what it holds
  cannot be fixed by hand. Its lines are marked `(deleted page)`, and a value
  or an empty list entry there is settled in the decisions file like any
  other. Its draft is never read again, so where no decision can settle why
  it cannot be rewritten (it no longer validates, a value has a shape the
  walk refuses), the draft is left as it is and said, and the store goes on;
  its versions are moved as usual. What a deleted page's draft says about an
  image's alt decides nothing.
- **A decision reaches every version.** A version is immutable to the editor,
  so a value only an old version holds can be settled nowhere else. Like the
  rest of this command, a decision changes the content of every draft and
  every version that holds the value, published and archived ones included.
- **A list keeps its pairs.** `hero_carousel` has two lists,
  `images` → `artworks` and `mobileImages` → `mobileArtworks`, and the default
  design draws the phone image at the position of the desk one. A list of
  references has no empty slot (the storefront's schema takes an asset id from
  1 up, and a release refuses an id that names no asset), so an empty entry, as
  written or by an `images` decision, is handled by position. A position that
  is empty in every list of the block goes from each, which moves no pair. An
  empty entry beside an image of the other list stops the store, because
  dropping it alone would put every later image against the wrong partner. The
  report names it as `<document> <draft | version N>: <pointer> (<block>)` and
  shows the entry of each list at that position, so what a removal would drop
  is in front of the person deciding. Under `slots`, with exactly that name up
  to the pointer, they state one of two things: the id of a library image to
  put there, or `"remove"`, which takes that position out of every list of the
  block, its partner included. A draft and each version are decided
  separately, because each may hold something else at that position.
- **The rule does not look at the store's design.** Which component draws a
  block is the storefront's choice by store (`componentRegistry`,
  `<block>@<schema>`), and Django does not know it: webside's carousel does
  not pair the two lists at all, the default one does. A stored document is
  also not tied to the design that draws it today. So an empty entry beside an
  image stops every store and asks, whatever would draw it.
- A new setting already chosen stays and the URL beside it goes.
- `--dry-run` takes every step of the real run, the alt text and the release
  included, in a transaction it rolls back. A second run finds nothing.
- **Editors need not be idle.** A rewritten draft is a new revision, so an
  editor that had the page open is answered the usual revision conflict on its
  next save (the editor's "this page changed" notice) and reloads the new
  content. It cannot write the old settings back.
- **Share-preview links are not rewritten.** A link's snapshot is outside the
  version history and lives at most `BUILDER_PREVIEW_LINK_TTL_SECONDS`. It keeps
  the URL it froze, which the storefront's read adapter still draws, so a link
  made before the run opens as before. The adapter must outlive the last such
  link: remove it no sooner than that period after the last store was moved.

### A rewrite in place, and the way back

`persist_block_upgrades` and `migrate_builder_media` change every draft and
every version of a store, which nothing else may edit, and neither model
keeps history. `src/builder/document_rewrite.py` is the one place they do it.

- **Every document**, a deleted page's too: its versions are still stored, so
  its draft is moved with them.
- **A draft moves to a new revision.** Its content changed under whoever has
  it open, so a save that names the old revision is refused like any other
  late save. What a revision means is otherwise kept: a draft that was at its
  published revision stays there, and a publish waiting for its time names
  the new revision of the draft it froze.
- **Lock order.** The store's release sequence first, then the documents by
  key: the order of `publish`, `rollback`, `delete_page` and a scheduled
  publish. A rewrite and a restore end with a release, and documents locked
  first would deadlock with a publish arriving in between. Library images
  come last, by id (`media_services.lock_assets`): an edit or a delete of an
  image holds its row and takes no other lock after it, so nothing waits the
  other way round. A command reads the store to plan what it writes only
  after `document_rewrite.lock_store`.
- **What is replaced is kept.** Before a row is rewritten, the columns about
  to be replaced are copied into a `BuilderRewriteBackup`, one per draft and
  per version, in the same transaction, under the run (`BuilderRewriteRun`:
  its id, the command, the time, what was decided). Each command prints
  `backup run <id>` for a store it wrote. A draft's old content is not kept as
  a version instead: a version is the store's own history (its numbers, its
  restore list), and one in the old shape would be the next thing to rewrite.

`manage.py builder_rewrite_backups --tenant <schema>` lists the runs a store
holds: id, time, command, number of backups, and the decisions. A run id that
no store the command reaches holds, given to `--restore` or `--delete`, exits
1: it is a mistyped id.

- `--restore <run>` (with `--dry-run` to try it) puts a run back in that
  store, **whole or not at all**. Each draft and version gets its columns
  exactly as they were, with no validation, and its backup is deleted; a
  restored draft moves to a new revision again. A row that no longer holds
  what the run stored cannot be put back: a draft saved since is someone's
  work, a version rewritten since belongs to a later run (restore that one
  first). One such row and nothing of the store is restored, the rows are
  named, and the command exits 1.
- `--leave-changed-rows` restores the rest of the run all the same and leaves
  those rows as they are, their backups kept. The store then holds both
  shapes, which the storefront reads, and the command still exits 1.
- A restore ends with a new release of the same versions, like the rewrite
  did. The command that made the run can then be run again.
- Alt text `migrate_builder_media` wrote onto an image stays after a restore.
  An image's alt is edited in the library, and the next run finds it there.
- `--delete <run>` removes a run and its backups, and with them the way back.
  Nothing else does: there is no sweep. It prints what it would delete (the
  run's time, command and number of backups, per store) and deletes only with
  `--yes`. One run id spans every store of its invocation, so `--all-tenants`
  is refused unless `--in-every-store` is given too. The latest run of a
  store is kept unless `--latest-too` is given.
- A backup is a copy of a draft or a version, so it is the same kind of data
  as those (`src/devtools/anonymise/rules.py`), and Django deletes it with its
  document or its version.

**Rolling back to a release from before this table.** That release deletes
versions (checkpoint pruning in `src/builder/services.py`) and pages without
knowing the backups exist. Django's cascade is Django's: the database would
not delete a backup, it would refuse the delete of the version. A
database-level cascade is not available here (`DB_CASCADE` is refused by the
system check `fields.E323`, because `BuilderVersion` reaches its document
with a Python-level one). So the two references from a backup carry no
database constraint (`db_constraint=False`): the older release deletes what
it deletes, a store can be rolled back with its backups in place, and a
backup left without its version has nothing to restore and is removed at the
next restore of its run.
