"""Record each uploaded image's real size in the rich text that shows it.

TinyMCE writes an uploaded image as ``<img src=... width="100%"
height="100%">``: the percentages are the author's layout (how much of
the column the picture fills), not its pixels. The storefront needs the
pixels to ask the media service for sizes that fit the screen and to
reserve the space before the file arrives, and nothing in the markup
says them, so it requested one fixed width for every device.

The pixels go in ``data-natural-width`` / ``data-natural-height``, the
names ``HTMLImageElement.naturalWidth`` / ``naturalHeight`` give them,
and never in ``width`` / ``height``: replacing the author's ``100%`` or
``75%`` with pixels would change how the page lays out.

Only a file in the saving store's own media directory is measured, by
reading its header from storage. A URL is never fetched: a ``src`` that
points anywhere else carries no size, and a size left over from an
earlier ``src`` is removed rather than kept.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.db import connection
from PIL import Image, UnidentifiedImageError

from core.utils.sanitize import NATURAL_HEIGHT_ATTR, NATURAL_WIDTH_ATTR
from tenant.storage import TenantFileSystemStorage

# EXIF orientations that turn the picture a quarter turn: a browser
# shows such a file with its width and height swapped.
_EXIF_ORIENTATION_TAG = 0x0112
_QUARTER_TURNS = frozenset({5, 6, 7, 8})


class _ImgTags(HTMLParser):
    """The source text of every ``<img>`` start tag, in document order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.tags.append((self.get_starttag_text() or "", dict(attrs)))

    handle_startendtag = handle_starttag


def _media_name(src: str) -> str | None:
    """``src`` as a name in this store's media storage, or ``None``.

    Matches on the path, not the host: a store's images are served from
    its own static domain, which is not the platform's ``MEDIA_URL``
    host, and the host decides nothing here because the file is read
    from local storage either way.
    """
    prefix = f"{urlsplit(settings.MEDIA_URL).path}{connection.schema_name}/"
    path = unquote(urlsplit(src).path)
    if not path.startswith(prefix) or len(path) == len(prefix):
        return None
    return path[len(prefix) :]


def natural_size(
    src: str, storage: TenantFileSystemStorage
) -> tuple[int, int] | None:
    """The size a browser shows the stored file at, or ``None``."""
    name = _media_name(src)
    if name is None:
        return None
    try:
        with storage.open(name) as stored, Image.open(stored) as image:
            width, height = image.size
            orientation = image.getexif().get(_EXIF_ORIENTATION_TAG)
    except (
        OSError,
        ValueError,
        SuspiciousFileOperation,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ):
        # Missing, not an image (an SVG, a truncated upload) or outside
        # the store's directory: no size to record.
        return None
    if orientation in _QUARTER_TURNS:
        width, height = height, width
    return width, height


def _with_natural_size(tag: str, size: tuple[int, int] | None) -> str:
    """``tag`` with its natural-size attributes set to ``size``.

    The sanitiser has already run, so every attribute is double quoted
    and an earlier pair is found by exact text.
    """
    for attr in (NATURAL_WIDTH_ATTR, NATURAL_HEIGHT_ATTR):
        start = tag.find(f" {attr}=")
        if start != -1:
            end = tag.find('"', tag.find('"', start) + 1) + 1
            tag = tag[:start] + tag[end:]
    if size is None:
        return tag
    closing = "/>" if tag.endswith("/>") else ">"
    body = tag[: -len(closing)].rstrip()
    return (
        f'{body} {NATURAL_WIDTH_ATTR}="{size[0]}" '
        f'{NATURAL_HEIGHT_ATTR}="{size[1]}"{closing}'
    )


def record_natural_sizes(html: str) -> str:
    """``html`` (already sanitised) with each image's natural size set.

    Idempotent: a second pass over its own output measures the same
    files and returns the same text.
    """
    if not html or "<img" not in html:
        return html
    parser = _ImgTags()
    parser.feed(html)
    parser.close()

    storage = TenantFileSystemStorage()
    out: list[str] = []
    cursor = 0
    for tag, attrs in parser.tags:
        start = html.find(tag, cursor)
        if start == -1:
            continue
        src = attrs.get("src")
        size = natural_size(src, storage) if src else None
        out.append(html[cursor:start])
        out.append(_with_natural_size(tag, size))
        cursor = start + len(tag)
    out.append(html[cursor:])
    return "".join(out)
