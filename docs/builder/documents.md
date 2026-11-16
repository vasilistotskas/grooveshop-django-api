# Page builder: documents, validation and references

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
public read answers each document's `settings` already localised. A kind with a
root takes that overlay even when the draft has no `settings` (the editor drops
an empty one, and a store may write the English title before the Greek one): it
is checked against the root's schema over no settings, and the read answers the
translation alone.

Ids match `[A-Za-z0-9_-]{1,64}` and are unique across the whole document
(the overlay addresses nodes by id). `order` and `blockOrder` list every
child exactly once.

## The catalogue is the storefront's

The storefront declares each block once and serves the result per store:
`GET {NUXT_INTERNAL_BASE_URL}/api/builder/catalogue?schema=<schema>` answers
`{hash, blocks: [...]}` with `ETag: "<hash>"` (`src/builder/storefront.py`; the
base URL helper and `StorefrontUnavailable` are `src/core/storefront.py`, shared
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

`src/builder/validation.py` collects every problem as `{path, code, message}`,
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

A draft nobody opened since its block moved would therefore be refused at
publish, and a release written before a block took data settings records no
dependency rows for them. `manage.py persist_block_upgrades --tenant <schema>`
(or `--all-tenants`; `--dry-run` does it all and rolls it back) writes the
upgrades into every stored draft and version, each validated against the
store's catalogue, then rebuilds the current release's reference index. One
document that does not pass stops its store with nothing written; a feature
switched off does not count against a document here, as it does at a publish,
but one the platform does not know does. An older storefront leaves out a node
newer than it knows, so run it once the storefront release it follows has
proved itself. It rewrites a `BuilderVersion` in place
(`src/builder/document_rewrite.py`, [the way back](media.md#a-rewrite-in-place-and-the-way-back)
): a version keeps its number and what it renders. A draft moves to a
new revision and stays published if it was, so a store is not told it has
unpublished changes.
`src/builder/block_upgrades.py` is a port of the storefront's `*Upgrades.ts`, held
to `tests/fixtures/builder/upgrade-cases.json`. A block that gains a version
adds its step there too, or the command stops at it with `no upgrade from`.
The overlay moves with the node: each language's settings are upgraded too, and
what differs from the draft's upgraded text stays as that language's translation
(`upgrade_translations`), so an upgrade can rename, merge or drop a translated
setting and every language reads what it read.

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


### A theme keeps its contrast (`src/builder/contrast.py`)

The catalogue declares, for the theme document, the pairs of colours that must
keep a WCAG 2.2 contrast (`documentKinds.theme.contrast`): `{id, foreground,
background, min, severity, label}`, each side an RFC 6901 pointer into the theme's
root settings, a literal `#RRGGBB`, or a colour as the page draws it mixed,
`{mix, toward, percent}` (CSS `color-mix(in oklab, <mix>, <toward>
<percent>%)`, the dark accent as text); and `label` the rule's name in every
language of `settings.LANGUAGES` (`{"el": ..., "en": ...}`). The catalogue is
the one place a rule is named: the editor's theme panel and Publish dialog read
the same `label`. A rule's other keys (the editor's `fix`) are not read. The
theme stores its colour scales, so the rules read stored colours; the one
colour derived is a mix, `contrast.mix_oklab` (OKLab with CSS Color 4's
matrices, clipped to sRGB, rounded to 8 bits), the storefront's `mixOklab`
held to the same `mixes` vectors.

- **Saving never refuses for contrast.** The editor shows every rule.
- **A release blocks on the `error` rules** (`validation.check_before_release`,
  used by `publish`, `publish/check` and scheduling): an `insufficient_contrast`
  problem with `document: "theme"`, the pointer side of the rule as `path` (the
  foreground when both are pointers, `/settings` when neither is) and `params`
  `{rule, ratio, min, label-<language>...}` as strings: `rule` is the id the
  editor leads to, and the name in every language lets a kept failure be worded
  again in the language that reads it (`errors.message_for`, the scheduled
  publish email). The message names the rule by its `label` in the request's
  language. The ratio is compared unrounded.
- **A rule that points at no colour is a catalogue bug**, answered `503
  invalid_catalogue`, never a pass. A malformed rule, or one not named in
  every language, is refused when the catalogue is read.
- Rolling back or republishing a stored version is not re-judged: it was valid
  when it was released.

`tests/fixtures/builder/contrast-cases.json` (the storefront's, copied and
pinned) holds the ratios, the boundary cases and four whole themes; the
storefront runs the same file.

### Where a store's theme document comes from

Django resolves no preset and does no colour maths: the storefront owns what a
theme is.

- **A new store:** provisioning's `theme` step installs
  `documentKinds.theme.defaults` of THE STORE'S catalogue view (a design's
  defaults) as its theme document, validated like a publish and released in its
  own release (`builder.seeding.install_theme_defaults`). A catalogue without
  defaults installs nothing; a catalogue that cannot be fetched or read fails
  the step at ERROR (the store has no theme document, and `seeding_failures`
  names `theme` to the operator), and re-running provisioning finishes it (a
  store with a theme is left alone). A catalogue the storefront serves and
  Django cannot read (a storefront older than the catalogue shape this release
  requires) is logged at ERROR by `fetch_catalogue` with the storefront and the
  reason; every caller then refuses. The platform's own `Theme` release does not count as work an editor
  made (`seed_with_content` still applies).


### Typed data props (`src/builder/data_sources.py`)

A block can declare a setting in the catalogue's `data` map, beside `refs`
(both are read until the storefront stops serving `refs`):

- `{field: "ref", kind, onMissing}`: the setting holds one record's id (a slug
  for a page), checked like a `refs` entry;
- `{field: "query", source, onEmpty, maxLimit}`: the setting holds a descriptor,
  a hand-picked list (`{mode: "picked", ids}`) or a rule
  (`{mode: "filter", ...}`, `{mode: "roots" | "children", ...}`, `{limit}`),
  for `source` in `product`, `blogPost`, `category`, `blogCategory`,
  `promotion`.

A product rule (`{mode: "filter", ...}`) takes `categories`, `ordering`
(`newest`, `mostViewed`, `mostLiked`, `topRated`, `discounted`, `priceAsc`,
`priceDesc`), `limit` and an optional `stock`: `any` (or no key) ignores
stock, `inStockFirst` puts products in stock before sold-out ones (each group
in the chosen ordering), `inStockOnly` leaves sold-out products out. Any other
value is `invalid_data_value`; a hand-picked list takes no `stock`
(`unknown_filter`). Stock does not change the facets of the list (see the
purge section below).

`data_sources.check(declaration, value)` judges a value and says what it
names. A value that is not valid yields problems and nothing else:
`invalid_data_value`, `unknown_filter` (a key the list does not have) and
`too_many_ids`, each at the pointer of the offending key; a document that
asks for more lists than the catalogue's `dataLimits.maxQueriesPerDocument`
gets `too_many_queries`. The caps (`maxPickedIds`, `maxFilterIds`,
`maxQueriesPerDocument`, `maxQueryLimit`) live in the catalogue only; a
catalogue that declares `data` without `dataLimits` is refused as unexpected.
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


### A changed record reaches the pages that show it (`src/builder/data_purge.py`)

The storefront caches a rendered page, so a product, category, post or
promotion that changes must purge the pages that show it. The index above
answers which: `documents_for` looks up the documents of the current release
that name the record (a `product` row) or ask for a list it can enter or leave
(a `queryProduct` row whose facet is `*` or one of the record's categories,
WITH its ancestors, in its old and its new value).

Receivers on `Product`, `ProductCategory`, `BlogPost`, `BlogCategory`,
`Promotion` and their translations compute the affected documents when the
transaction commits and queue them in a per-store Redis set; the batched purge
(`core.cache.invalidation`) then sends the storefront `builderDocuments`
(document keys, at most 64 per call, `patterns` empty) through the
`builder_documents` surface, and the storefront maps a key to its own routes.
A save of `stock` or `updated_at` alone queues nothing, unless it sells the
product out or brings it back (stock crossing zero, `product.availability`): a
list that puts products in stock first or leaves sold-out ones out then shows
something else, so the save reaches the same pages a product edit does. The
index records a list's facets, not its `stock` rule, so every list is reached.
That one definition of "this stock save matters" serves both purges: the
documents here and the `products` and `sitemap_seo` cache surfaces
(`core.cache.invalidation.register_significant_save`), which are the data the
pages are rebuilt from (`builderDataProduct`), the product pages and the
listing. A `pre_save` of the product reads the stored category and stock in
one query, so a sale costs no query for either purge. In a batched purge the
surfaces that purge documents run last, so a page rendered in between never
stores the data cache that is about to go. A promotion reaches
the offer lists and every page that shows a product. A write that sends no
`post_save` (a queryset `update()` of stock, as the demo reset's restore in
`src/devtools/demo_account.py: _refresh_restocked`) queues the same documents
with `data_purge.record_products`. Over-purging is safe;
under-purging is the bug, so a list is matched by ANY of its facets.
