"""Which images the platform accepts, decided once.

An upload is served by the media service (grooveshop-media-stream), which
refuses a source above its per-format byte cap and shows the placeholder
instead. So the API refuses, when the image is uploaded, what that service
would refuse when it is shown. The limits are the media service's own, copied
into ``core/data/image-limits.json`` (pinned by git id, refreshed by
``.github/scripts/sync_upstream_sources.py``), never written down a second
time here.

``validate_servable_image`` is the one rule. It is applied to a NEW upload only
(the admin form field, the TinyMCE form, the API's serializer field, the page
builder's media library), never to the stored file of a model on every save, so
an image that predates the rule can still be edited around
(``manage.py report_unservable_media`` lists them).
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

import defusedxml.ElementTree as ET
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from PIL import Image, UnidentifiedImageError

from core.utils.rich_text_images import displayed_size

if TYPE_CHECKING:  # pragma: no cover
    from django.core.files import File

LIMITS_FILE = Path(__file__).resolve().parent / "data" / "image-limits.json"

#: Pillow's name for a format, to the name the media service's contract uses.
PILLOW_FORMATS: Mapping[str, str] = {
    "JPEG": "jpeg",
    "PNG": "png",
    "WEBP": "webp",
    "GIF": "gif",
    "TIFF": "tiff",
    "AVIF": "avif",
}

#: A raster format the media service serves, to its MIME type.
RASTER_MIME: Mapping[str, str] = {
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "tiff": "image/tiff",
    "avif": "image/avif",
}
SVG = "svg"

_BYTES_PER_MB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ImageLimits:
    """What the media service accepts as a source image."""

    max_file_bytes: Mapping[str, int]
    source_pixel_limit: int

    @property
    def formats(self) -> frozenset[str]:
        return frozenset(self.max_file_bytes)

    @property
    def largest_file_bytes(self) -> int:
        return max(self.max_file_bytes.values())

    @property
    def max_pixels(self) -> int:
        """The most pixels one upload may have: this platform's own budget
        (``UPLOAD_IMAGE_MAX_PIXELS``), never above what the media service
        decodes."""
        return min(settings.UPLOAD_IMAGE_MAX_PIXELS, self.source_pixel_limit)


@cache
def image_limits() -> ImageLimits:
    """The limits of the contract file, read once."""
    contract = json.loads(LIMITS_FILE.read_text(encoding="utf-8"))
    return ImageLimits(
        max_file_bytes=dict(contract["maxFileBytes"]),
        source_pixel_limit=int(contract["sourcePixelLimit"]),
    )


@dataclass(frozen=True, slots=True)
class SourceImage:
    """An accepted upload: its format (the contract's name), MIME type and,
    for a raster, the size a browser shows (EXIF orientation applied)."""

    format: str
    mime: str
    width: int | None
    height: int | None


def _megabytes(size: int) -> str:
    return f"{size / _BYTES_PER_MB:g}"


def _refuse(message: str, code: str) -> ValidationError:
    return ValidationError(message, code=code)


def _identify(file: IO[bytes]) -> tuple[str, Image.Image] | None:
    """``(contract format, opened image)`` of a raster Pillow reads, else
    ``None``. An image Pillow reads in a format the media service does not
    serve (a BMP, an ICO) is refused as unsupported."""
    try:
        image = Image.open(file)
    except OSError, SyntaxError, UnidentifiedImageError, ValueError:
        return None
    format_name = PILLOW_FORMATS.get(str(image.format))
    if format_name is None:
        image.close()
        raise _refuse(
            _("Only %(formats)s images can be uploaded.")
            % {"formats": _format_list(RASTER_MIME)},
            "unsupported_image_type",
        )
    return format_name, image


def _format_list(formats: Collection[str]) -> str:
    return ", ".join(name.upper() for name in formats)


def validate_servable_image(
    file: Any,
    *,
    formats: Collection[str] | None = None,
    allow_svg: bool = True,
) -> SourceImage:
    """Refuse *file* unless the media service will serve it; return what it is.

    - it is a raster Pillow reads in a format of the contract, or (with
      ``allow_svg``) a safe SVG;
    - ``formats`` narrows that for one use (a subset of the contract's names);
    - it is within the byte cap of ITS format (sniffed from the content, never
      from the name);
    - a raster is within the pixel budget.

    ``ValidationError`` codes: ``invalid_image``, ``unsupported_image_type``,
    ``file_too_large``, ``image_too_large``, and the SVG codes of
    :func:`is_safe_svg`. The file is rewound.
    """
    limits = image_limits()
    allowed = (
        limits.formats if formats is None else limits.formats & set(formats)
    )
    if not allow_svg:
        allowed = allowed - {SVG}
    size = getattr(file, "size", None)
    if not size:
        raise _refuse(_("The file is not a valid image."), "invalid_image")
    try:
        file.seek(0)
        identified = _identify(file)
        if identified is None:
            return _svg(file, size, allowed, limits)
        format_name, image = identified
        with image:
            if format_name not in allowed:
                raise _refuse(
                    _("Only %(formats)s images can be uploaded.")
                    % {"formats": _format_list(sorted(allowed))},
                    "unsupported_image_type",
                )
            _within_bytes(size, format_name, limits)
            width, height = image.size
            if width * height > limits.max_pixels:
                raise _refuse(
                    _("The image has more than %(limit)s megapixels.")
                    % {"limit": limits.max_pixels // 1_000_000},
                    "image_too_large",
                )
            image.verify()
            file.seek(0)
            with Image.open(file) as reopened:
                displayed = displayed_size(reopened)
    except Image.DecompressionBombError as exc:
        raise _refuse(
            _("The image has more than %(limit)s megapixels.")
            % {"limit": limits.max_pixels // 1_000_000},
            "image_too_large",
        ) from exc
    except (OSError, SyntaxError, ValueError) as exc:
        raise _refuse(
            _("The file is not a valid image."), "invalid_image"
        ) from exc
    finally:
        with contextlib.suppress(OSError, ValueError):
            file.seek(0)
    return SourceImage(
        format_name, RASTER_MIME[format_name], displayed[0], displayed[1]
    )


def _within_bytes(size: int, format_name: str, limits: ImageLimits) -> None:
    cap = limits.max_file_bytes[format_name]
    if size > cap:
        raise _refuse(
            _("The file is larger than %(limit)s MB.")
            % {"limit": _megabytes(cap)},
            "file_too_large",
        )


def _svg(
    file: Any, size: int, allowed: Collection[str], limits: ImageLimits
) -> SourceImage:
    """The upload is not a raster: accept it if it is a safe SVG the media
    service takes. The size is checked BEFORE the parse, so a huge file is
    never fed to the XML parser."""
    if SVG not in allowed:
        raise _refuse(_("The file is not a valid image."), "invalid_image")
    _within_bytes(size, SVG, limits)
    if not is_safe_svg(file):
        raise _refuse(_("The file is not a valid image."), "invalid_image")
    return SourceImage(SVG, "image/svg+xml", None, None)


def is_safe_svg(f: File[Any] | None) -> bool:
    """Whether *f* is a well-formed SVG with nothing in it that runs.

    ``ValidationError`` for one that is an SVG but carries script, a
    ``foreignObject``, an event handler or a ``javascript:`` URL."""
    if f is None:
        return False

    SVG_TAGS = ["{http://www.w3.org/2000/svg}svg", "svg"]

    original_position = None
    if hasattr(f, "tell") and callable(f.tell):
        with contextlib.suppress(OSError):
            original_position = f.tell()

    if hasattr(f, "seek") and callable(f.seek):
        f.seek(0)

    try:
        doc = ET.parse(f)
        root = doc.getroot()

        if root.tag not in SVG_TAGS:
            return False

        for elem in root.iter():
            tag_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag_name.lower() == "script":
                raise ValidationError(
                    _(
                        "SVG files containing script elements are not allowed for security reasons."
                    ),
                    code="svg_script_not_allowed",
                )

            # <foreignObject> can embed arbitrary HTML (incl. scripts),
            # bypassing the SVG-only checks (G0098).
            if tag_name.lower() == "foreignobject":
                raise ValidationError(
                    _(
                        "SVG files containing foreignObject are not allowed for security reasons."
                    ),
                    code="svg_foreign_object_not_allowed",
                )

            for attr in elem.attrib:
                if attr.lower().startswith("on"):
                    raise ValidationError(
                        _(
                            "SVG files containing event handlers are not allowed for security reasons."
                        ),
                        code="svg_event_handler_not_allowed",
                    )

                # href / xlink:href / src carrying a javascript: or
                # script-capable data: URL is an XSS vector the script /
                # on* checks miss (G0098). Strip whitespace first so
                # "java\nscript:" can't evade the prefix check.
                attr_local = attr.split("}")[-1].lower()
                if attr_local in {"href", "src"}:
                    value = re.sub(
                        r"\s+", "", (elem.attrib[attr] or "")
                    ).lower()
                    if value.startswith(
                        (
                            "javascript:",
                            "data:text/html",
                            "data:image/svg",
                        )
                    ):
                        raise ValidationError(
                            _(
                                "SVG files with javascript: or data: URLs are not allowed for security reasons."
                            ),
                            code="svg_dangerous_url_not_allowed",
                        )

        return True

    except ET.ParseError, ValueError:
        return False
    finally:
        if hasattr(f, "seek") and callable(f.seek):
            try:
                if original_position is not None:
                    f.seek(original_position)
                else:
                    f.seek(0)
            except OSError:
                pass


def stored_image_problem(path: Path) -> str | None:
    """Why the media service would not serve the stored file *path*, or
    ``None``: its sniffed format, byte size and pixel count against the
    contract. Reads the header only. Used by the read-only report; an upload
    is judged by :func:`validate_servable_image`."""
    limits = image_limits()
    size = path.stat().st_size
    try:
        with path.open("rb") as handle:
            identified = _identify(handle)
            if identified is None:
                head = handle.read(2048).lstrip()
                if head.startswith((b"<?xml", b"<svg")) or b"<svg" in head:
                    cap = limits.max_file_bytes[SVG]
                    return (
                        f"svg {_megabytes(size)} MB over the {_megabytes(cap)} MB cap"
                        if size > cap
                        else None
                    )
                return "not an image the media service can read"
            format_name, image = identified
            with image:
                cap = limits.max_file_bytes[format_name]
                if size > cap:
                    return (
                        f"{format_name} {_megabytes(size)} MB over the "
                        f"{_megabytes(cap)} MB cap"
                    )
                width, height = image.size
                if width * height > limits.source_pixel_limit:
                    return (
                        f"{format_name} {width}x{height} over the "
                        f"{limits.source_pixel_limit} pixel limit"
                    )
    except ValidationError as exc:
        return exc.messages[0]
    except OSError, ValueError, Image.DecompressionBombError:
        return "not an image the media service can read"
    return None
