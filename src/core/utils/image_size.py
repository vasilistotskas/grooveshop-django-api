"""The size the media service (grooveshop-media-stream, Sharp on libvips)
shows a stored raster at: its stored size, with width and height swapped
when its EXIF orientation is a quarter turn, as Sharp rotates it before
resizing. Every place that records an image's size reads it here: the
image model field and its backfill, an upload's validation, and the sizes
written into rich text.
"""

from __future__ import annotations

from typing import IO

from PIL import Image, UnidentifiedImageError

# EXIF orientations that turn the picture a quarter turn: the media
# service serves such a file with its width and height swapped.
_EXIF_ORIENTATION_TAG = 0x0112
_QUARTER_TURNS = frozenset({5, 6, 7, 8})


def displayed_size(image: Image.Image) -> tuple[int, int]:
    """The size the media service shows the opened *image* at.

    A PNG's EXIF orientation counts only when its ``eXIf`` chunk precedes
    the pixel data, the only place libvips reads it (a quarter-turn tag
    after ``IDAT`` is served unrotated). Pillow has read such a chunk by
    the time the file is open; asking it for one it has not seen makes it
    decode the whole image to look past the pixels.
    """
    width, height = image.size
    if image.format == "PNG" and "exif" not in image.info:
        return width, height
    if image.getexif().get(_EXIF_ORIENTATION_TAG) in _QUARTER_TURNS:
        width, height = height, width
    return width, height


def displayed_dimensions(file: IO[bytes]) -> tuple[int, int] | None:
    """:func:`displayed_size` of the raster *file*, or ``None`` for a file
    Pillow cannot read as an image (an SVG) or cannot read at all
    (truncated, corrupt): no size is truer than a wrong one.

    Reads the header only (and a JPEG's or WebP's EXIF segment); the
    file's position is restored.
    """
    position = file.tell() if file.seekable() else None
    try:
        file.seek(0)
        with Image.open(file) as opened:
            return displayed_size(opened)
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        return None
    finally:
        if position is not None:
            file.seek(position)
