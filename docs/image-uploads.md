# Image uploads

One rule decides which images the platform accepts:
`core.uploads.validate_servable_image`. It exists because every uploaded image
is served by the media service (grooveshop-media-stream), which refuses a
source above its per-format byte cap and shows the placeholder instead. So the
API refuses, when the image is uploaded, what the media service would refuse
when it is shown.

## The limits are the media service's, copied

`contract/image-limits.json` in grooveshop-media-stream holds the per-format
byte caps and the pixel limit it holds a source to (a spec there keeps the file
equal to its constants; `docs/image-limits.md` there says what is refused).
`src/core/data/image-limits.json` is a pinned COPY:
`.github/scripts/sync_upstream_sources.py` writes it and
`tests/fixtures/upstream-sources.lock.json` pins it by git id,
`tests/unit/core/test_upstream_sources_pin.py` fails on a hand edit and the
weekly `upstream-sources.yml` workflow fails when the media service has
changed it. A refresh is: run the script, run `tests/unit/core/test_uploads.py`
and the builder media tests, commit. Nothing reads the media service at
runtime.

## The rule

Every upload path calls `core.uploads.prepare_servable_image`. It first makes
the upload fit (below), then judges what comes out with
`validate_servable_image`, so nothing the rule refuses gets through.

- The format is sniffed from the content (Pillow), never taken from the name
  or the declared type: a JPEG named `.png` is a JPEG with a JPEG cap.
- A raster in a format the media service does not serve (BMP, ICO) is
  `unsupported_image_type`; a safe SVG is accepted where SVG is allowed (the
  SVG sanitiser is unchanged: no script, `foreignObject`, event handlers or
  `javascript:` URLs), at the SVG cap, checked before it is parsed.
- At most the format's byte cap (`file_too_large`).
- At most `UPLOAD_IMAGE_MAX_PIXELS` (default 40 megapixels, and never above the
  media service's own limit) (`image_too_large`).
- Anything else is `invalid_image`.

### A big photo is shrunk, not refused

A raster over its format's byte cap or over the pixel budget is re-encoded in
the SAME format by `src/core/image_normalise.py`:

- the EXIF orientation is applied, then the image is scaled down to
  `UPLOAD_IMAGE_MAX_EDGE` on its long side (default 4096, twice the widest
  rendition the media service makes) at `UPLOAD_IMAGE_QUALITY` (lossy
  formats), and scaled further in steps of 25% until it fits its cap;
- the colour profile is kept; nothing is ever enlarged;
- it is refused (`file_too_large`) only when no step fits the cap;
- an upload within its caps is returned untouched.

GPS is removed from EVERY raster upload, not only a shrunk one: an EXIF GPS
block or an XMP packet naming GPS fields makes the file be re-encoded without
it, at its own size (the orientation tag stays; the rest of the EXIF stays).
An animated GIF or WebP cannot be resized or stripped without flattening it,
so one that needs either is refused (`animated_image_not_normalisable`); one
within its caps and carrying no position passes unchanged. SVG is never
re-encoded.

It is applied to a NEW upload only, never to a stored file on every save, so an
image stored before the rule can still be edited around it.

## Where it applies

| Path | How |
|---|---|
| Every model field that is an `ImageAndSvgField` (admin forms, blog, categories, products, avatars, shipping logos, pay way icons, loyalty tiers, country flags) | `core.forms.image.ImageAndSvgField.to_python` (the cleaned file is the shrunk one) |
| Those same fields through a DRF `ModelSerializer` | `CoreConfig.ready` maps the model field to `core.api.image_field.ServableImageField` |
| Product images through the API | `ServableImageField(formats=PRODUCT_IMAGE_FORMATS)`: a product shows a still, no GIF or TIFF |
| TinyMCE image upload (`core.views.upload_image`) | `ImageUploadForm` uses `ImageAndSvgField` |
| The page builder's media library (`src/builder/media_services.py`) | `validate_servable_image(allow_svg=False)` and the import is cut off at the largest cap |

`FILE_UPLOAD_MAX_MEMORY_SIZE` (2.5 MB) is Django's threshold for holding an
upload in memory or spooling it to disk. It is not a policy and no rule reads
it; the admin form's former flat 2.5 MB cap borrowed it and is replaced by the
caps above.

## Finding what is already over

`uv run python manage.py report_unservable_media [--schema NAME]` lists the
stored images in `MEDIA_ROOT` that the media service would not serve (over its
cap or pixel limit, or not an image). It is read only: it deletes, moves and
re-encodes nothing. What to do with a store's file is the store owner's call.
Files stored before the rule are never rewritten.
