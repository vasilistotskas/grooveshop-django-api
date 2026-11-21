# Storefront page builder (`src/builder/`)

A store's theme, section groups (header, footer, ...), templates and pages
are **documents**: versioned JSON that the store's staff edit in the
storefront's visual editor and publish together. Source: `src/builder/`
(`models.py`, `services.py`, `validation.py`, `storefront.py`,
`references.py`, `published.py`, `views/` (one module per resource), `rendering.py`,
`media_services.py`, `media_api.py`), the
`builder` cache surface in `src/builder/cache_surfaces.py`,
`src/tenant/role_scopes.py`. The code wins over this page.

`builder` is a tenant app: every table lives in the store's schema, with its
backups, staging refresh and destroy. The editor and the block catalogue are
the storefront's; Django stores, validates, versions and publishes.

## Which file to read

This is one reference in several files, so each fits in one read. Read
the file for what you are changing. The headings keep their names.

| File | Read it before changing |
|---|---|
| [`documents.md`](documents.md) | the model, a document, the storefront's catalogue, what a save checks, translations, references, the theme's contrast, typed data props, and what purges a page when a record changes |
| [`publishing.md`](publishing.md) | writes, what a release moves, the API and its declared refusals, the live preview, the editor's link pickers, share-preview links, presence, the feature switches, scheduled publishing |
| [`media.md`](media.md) | the media library, importing an image the store already shows, resolving media for a draft, images documents still name by URL, a rewrite in place and the way back |
| [`sign-in-and-defaults.md`](sign-in-and-defaults.md) | signing in to the editor, what is copied from the storefront, default documents for a new store, the demo store's pages, what is not here yet |

Keep each file well under 40,000 characters. A file that outgrows the
limit is split on a subject, and this table gains a row.
