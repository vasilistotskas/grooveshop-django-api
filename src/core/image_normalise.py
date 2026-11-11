"""Make an uploaded raster fit what the media service serves, instead of
refusing it.

A phone photograph is routinely larger than the media service's byte cap for
its format, and it carries the place it was taken in its metadata. This module
re-encodes such an upload in the SAME format:

- over its byte cap or the pixel budget: orientation applied, scaled down to a
  working long edge (``UPLOAD_IMAGE_MAX_EDGE``, twice the widest rendition the
  media service makes) and, if it still does not fit, scaled further in steps;
- carrying GPS (EXIF or an XMP packet): re-encoded without it, at its own size.

The colour profile is kept. An animated image cannot be resized or have its
location removed without flattening it, so one that needs either is refused.
Everything else is returned untouched. ``core.uploads.prepare_servable_image``
is the only caller and then judges the result by the one upload rule.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import IO, Any

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.translation import gettext as _
from PIL import ExifTags, Image, ImageOps

#: Pillow's format name -> how it is written back. ``lossy`` formats take the
#: quality setting; the others are re-encoded without loss.
LOSSY = frozenset({"JPEG", "WEBP", "AVIF"})
#: The quality of a re-encode that only removes metadata (nothing is resized,
#: so it should not cost visible quality).
METADATA_ONLY_QUALITY = 95
#: Each further attempt to fit a cap scales the long edge by this much.
SHRINK_FACTOR = 0.75
#: Attempts to fit a cap, and the smallest long edge worth keeping.
MAX_ATTEMPTS = 6
MIN_EDGE = 256

_GPS_IFD = ExifTags.IFD.GPSInfo
_ORIENTATION = ExifTags.Base.Orientation
#: An XMP packet names GPS fields with these.
_XMP_GPS_MARKER = b"GPSLatitude"


@dataclass(frozen=True, slots=True)
class Rules:
    """What an upload of one format has to fit."""

    max_bytes: int
    max_pixels: int
    max_edge: int
    quality: int


def has_gps(image: Image.Image, raw: bytes) -> bool:
    """Whether the file carries a position: an EXIF GPS block or an XMP packet
    naming GPS fields."""
    if image.getexif().get_ifd(_GPS_IFD):
        return True
    return _XMP_GPS_MARKER in raw


def _refuse(message: str, code: str) -> ValidationError:
    return ValidationError(message, code=code)


def _clean_exif(image: Image.Image, *, orientation: bool) -> Image.Exif:
    """The image's EXIF without its GPS block (and, when the pixels were
    already turned upright, without the orientation tag)."""
    exif = image.getexif()
    if _GPS_IFD in exif:
        del exif[_GPS_IFD]
    if orientation and _ORIENTATION in exif:
        del exif[_ORIENTATION]
    return exif


def _encode(
    image: Image.Image,
    format_name: str,
    *,
    quality: int,
    icc_profile: bytes | None,
    exif: Image.Exif,
) -> bytes:
    buffer = io.BytesIO()
    options: dict[str, Any] = {}
    if icc_profile:
        options["icc_profile"] = icc_profile
    if format_name != "GIF":
        options["exif"] = exif.tobytes()
    if format_name in LOSSY:
        options["quality"] = quality
    if format_name in {"JPEG", "PNG", "GIF"}:
        options["optimize"] = True
    if format_name == "GIF":
        # A palette image cannot be resized well; go through RGBA and back.
        image = image.convert("RGBA").quantize(
            colors=256, method=Image.Quantize.FASTOCTREE
        )
    image.save(buffer, format=format_name, **options)
    return buffer.getvalue()


def _scaled(image: Image.Image, edge: int, max_pixels: int) -> Image.Image:
    """*image* no larger than *edge* on its long side and *max_pixels* in
    area. Never enlarges."""
    width, height = image.size
    factor = min(1.0, edge / max(width, height))
    if width * height * factor * factor > max_pixels:
        factor = (max_pixels / (width * height)) ** 0.5
    if factor >= 1.0:
        return image
    return image.resize(
        (max(1, int(width * factor)), max(1, int(height * factor))),
        Image.Resampling.LANCZOS,
    )


def normalise(
    file: IO[bytes], name: str, content_type: str, rules: Rules
) -> SimpleUploadedFile | None:
    """The re-encoded upload, or ``None`` when *file* needs nothing.

    ``ValidationError``: ``animated_image_not_normalisable`` for an animated
    image that needs a change, ``file_too_large`` when no attempt fits the
    cap, ``image_too_large`` for an image too large to decode safely.
    """
    file.seek(0)
    raw = file.read()
    try:
        with Image.open(io.BytesIO(raw)) as image:
            return _normalise(image, raw, name, content_type, rules)
    except Image.DecompressionBombError as exc:
        raise _refuse(
            _("The image has too many pixels to be resized."),
            "image_too_large",
        ) from exc


def _normalise(
    image: Image.Image,
    raw: bytes,
    name: str,
    content_type: str,
    rules: Rules,
) -> SimpleUploadedFile | None:
    format_name = str(image.format)
    width, height = image.size
    too_big = len(raw) > rules.max_bytes or width * height > rules.max_pixels
    located = has_gps(image, raw)
    if not too_big and not located:
        return None
    if getattr(image, "is_animated", False):
        raise _refuse(
            _(
                "An animated image cannot be resized or have its location "
                "removed. Upload a smaller or a still image."
            ),
            "animated_image_not_normalisable",
        )
    if Image.MAX_IMAGE_PIXELS and width * height > Image.MAX_IMAGE_PIXELS:
        raise _refuse(
            _("The image has too many pixels to be resized."),
            "image_too_large",
        )

    icc_profile = image.info.get("icc_profile")
    if not too_big:
        # Only the metadata goes: same pixels, orientation tag kept.
        image.load()
        data = _encode(
            image,
            format_name,
            quality=METADATA_ONLY_QUALITY,
            icc_profile=icc_profile,
            exif=_clean_exif(image, orientation=False),
        )
        return SimpleUploadedFile(name, data, content_type)

    if format_name == "JPEG":
        # Decode at a reduced scale when the file is far larger than needed.
        image.draft(None, (rules.max_edge * 2, rules.max_edge * 2))
    image.load()
    upright = ImageOps.exif_transpose(image)
    exif = _clean_exif(upright, orientation=True)
    edge = min(rules.max_edge, max(upright.size))
    for _attempt in range(MAX_ATTEMPTS):
        data = _encode(
            _scaled(upright, edge, rules.max_pixels),
            format_name,
            quality=rules.quality,
            icc_profile=icc_profile,
            exif=exif,
        )
        if len(data) <= rules.max_bytes:
            return SimpleUploadedFile(name, data, content_type)
        edge = int(edge * SHRINK_FACTOR)
        if edge < MIN_EDGE:
            break
    raise _refuse(
        _("The image cannot be reduced to fit the %(limit)s MB limit.")
        % {"limit": f"{rules.max_bytes / (1024 * 1024):g}"},
        "file_too_large",
    )
