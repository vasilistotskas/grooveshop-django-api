# Rich text

Every admin-authored HTML column is a `core.fields.rich_text.RichTextField`:
product and category descriptions, blog bodies and author bios, content
pages, payment instructions. One `pre_save` runs on every write path (the
admin, DRF, a shell, a command), in this order:

1. **Refuse lost content.** `core.utils.sanitize.lost_content` names any
   element, link address or media address the policy would drop; the save
   raises instead of silently stripping it.
2. **Sanitise.** `sanitize_html` reduces the markup to the allowlist in
   `core/utils/sanitize.py`. Residue no reader sees (`data-*`, event
   handlers, CSS custom properties such as Dark Reader's
   `--darkreader-inline-color` in a `style`) is dropped without refusing.
3. **Record image sizes.** `core.utils.rich_text_images.record_natural_sizes`.

The storefront re-sanitises the same markup on render, so it can only ever
show less than what is stored.

## Image sizes

TinyMCE writes an uploaded image as `<img src=... width="100%" height="100%">`.
Those percentages are the author's layout, not pixels, so the markup does not
say how big the picture is and the storefront cannot ask the media service for
a size that fits the screen or reserve the space.

Step 3 adds the file's real size beside the author's attributes:

```html
<img src="https://static.example/media/shop/uploads/tinymce/a.png"
     width="100%" height="100%"
     data-natural-width="1200" data-natural-height="800">
```

- The names are those of `HTMLImageElement.naturalWidth` / `naturalHeight`.
  `width` / `height` are never touched: replacing `100%` or `75%` with pixels
  would change the page layout.
- Only a file in the saving store's own media directory is measured: the
  `src` path must start with the path of `MEDIA_URL` plus the schema name
  (the host is ignored, because a store's images are served from its own
  static domain). The header is read from storage with Pillow; nothing is
  fetched over the network. EXIF quarter-turn orientations swap the two
  numbers, as a browser shows the file.
- Everything else (another host, a missing or non-image file, an SVG) carries
  no size, and a size left over from an earlier `src` is removed, so the pair
  is always the current file's.
- The attributes are computed on every save. They are on the `img` allowlist
  so a later save keeps them, but a hand-typed value is replaced.

### Backfill

Rows saved before this existed are brought up to date with

```bash
uv run python manage.py record_rich_text_image_sizes --all-tenants [--dry-run]
```

(`--tenant <schema>` for one store). It discovers the `RichTextField` columns
from the app registry, writes the column directly (no re-validation, history
or reindex, since no text a reader or the search index sees changes) and is
idempotent. The storefront serves its cached pages until they expire; run
`purge_edge_cache` afterwards to use the sizes at once.

Source: `core/fields/rich_text.py`, `core/utils/sanitize.py`,
`core/utils/rich_text_images.py`,
`core/management/commands/record_rich_text_image_sizes.py`.
