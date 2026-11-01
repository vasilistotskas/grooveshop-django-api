"""Every write of the media library.

An upload is measured and checked here before it is stored: only the
raster formats in ``BUILDER_MEDIA_FORMATS`` (an SVG can carry script, and
its size is not a size), at most ``BUILDER_MEDIA_MAX_BYTES`` and
``BUILDER_MEDIA_MAX_PIXELS``. The size recorded is the one a browser shows
(EXIF orientation applied), which is what the storefront reserves space
for.

A used asset cannot be deleted: the current release's reference index
(``builder.references``) names every document that points at it. Changing a
used asset's alt text or focal point reaches the storefront at once (cache
generation and the ``builder`` surface), because a release is immutable and
the media is resolved at render, not copied into it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import connection, transaction
from django.utils.translation import gettext as _
from PIL import Image, UnidentifiedImageError
from rest_framework import status

from builder import references
from builder.errors import BuilderError
from builder.models import MediaAsset, ReferenceKind
from builder.services import require_permission
from core.cache.invalidation import mark_surface_stale
from core.utils.i18n import available_language_codes
from core.utils.rich_text_images import displayed_size
from tenant.cache import bump_cache_generation
from tenant.staff_actors import staff_id

ADD_PERMISSION = "builder.add_mediaasset"
CHANGE_PERMISSION = "builder.change_mediaasset"
DELETE_PERMISSION = "builder.delete_mediaasset"

SURFACE = "builder"


class MediaInvalid(BuilderError):
    default_code = "invalid_media"
    default_detail = _("This file cannot be used.")


class MediaInUse(BuilderError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "media_in_use"
    default_detail = _("This image is used by a published page.")


def _inspect(upload: UploadedFile) -> tuple[str, tuple[int, int]]:
    """``(mime, displayed size)`` of an acceptable image, else refuse."""
    if upload.size is None or upload.size > settings.BUILDER_MEDIA_MAX_BYTES:
        raise MediaInvalid(
            _("The file is larger than %(limit)s MB.")
            % {"limit": settings.BUILDER_MEDIA_MAX_BYTES // (1024 * 1024)},
            code="file_too_large",
        )
    try:
        with Image.open(upload) as image:
            if image.format not in settings.BUILDER_MEDIA_FORMATS:
                raise MediaInvalid(
                    _("Only %(formats)s images can be uploaded.")
                    % {"formats": ", ".join(settings.BUILDER_MEDIA_FORMATS)},
                    code="unsupported_image_type",
                )
            width, height = image.size
            if width * height > settings.BUILDER_MEDIA_MAX_PIXELS:
                raise MediaInvalid(
                    _("The image has more than %(limit)s megapixels.")
                    % {"limit": settings.BUILDER_MEDIA_MAX_PIXELS // 1_000_000},
                    code="image_too_large",
                )
            mime = Image.MIME[str(image.format)]
            image.verify()
        upload.seek(0)
        with Image.open(upload) as image:
            size = displayed_size(image)
    except (
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ) as exc:
        raise MediaInvalid(
            _("The file is not a valid image."), code="invalid_image"
        ) from exc
    upload.seek(0)
    return mime, size


def _checked_focal(value: float | None) -> float:
    if value is None:
        return 0.5
    if not 0 <= value <= 1:
        raise MediaInvalid(
            _("A focal point is between 0 and 1."), code="invalid_focal_point"
        )
    return value


def _checked_alt(alt: Any) -> dict[str, str]:
    if not isinstance(alt, Mapping):
        raise MediaInvalid(
            _("Alt text is an object of language to text."), code="invalid_alt"
        )
    languages = set(available_language_codes())
    for locale, text in alt.items():
        if locale not in languages:
            raise MediaInvalid(
                _("%(locale)s is not a language of this store.")
                % {"locale": locale},
                code="unknown_locale",
            )
        if not isinstance(text, str) or len(text) > 255:
            raise MediaInvalid(
                _("Alt text is at most 255 characters."), code="invalid_alt"
            )
    return {locale: text.strip() for locale, text in alt.items()}


def _set_alt(asset: MediaAsset, alt: Mapping[str, str]) -> None:
    for locale, text in alt.items():
        asset.set_current_language(locale)
        asset.alt = text


def create_asset(
    user: Any,
    *,
    upload: UploadedFile,
    focal_x: float | None = None,
    focal_y: float | None = None,
    alt: Mapping[str, Any] | None = None,
) -> MediaAsset:
    require_permission(user, ADD_PERMISSION)
    mime, (width, height) = _inspect(upload)
    asset = MediaAsset(
        file=upload,
        width=width,
        height=height,
        mime=mime,
        size_bytes=upload.size,
        focal_x=_checked_focal(focal_x),
        focal_y=_checked_focal(focal_y),
    )
    asset.uploaded_by_staff_id = staff_id(user)
    _set_alt(asset, _checked_alt(alt or {}))
    try:
        with transaction.atomic():
            asset.save()
    except Exception:
        if asset.file and asset.file.name:
            asset.file.storage.delete(asset.file.name)
        raise
    return asset


def _reaches_the_storefront(asset: MediaAsset) -> None:
    """A used asset changed: the storefront resolves it at render, so its
    cached copies go, in the commit of the change."""

    def purge() -> None:
        bump_cache_generation(schema_name=connection.schema_name)
        mark_surface_stale(SURFACE)

    transaction.on_commit(purge)


def update_asset(
    user: Any,
    asset: MediaAsset,
    *,
    focal_x: float | None = None,
    focal_y: float | None = None,
    alt: Mapping[str, Any] | None = None,
) -> MediaAsset:
    """Change the focal point and the alt text of some languages; what is
    not named stays. The file never changes: a new image is a new asset."""
    require_permission(user, CHANGE_PERMISSION)
    new_alt = _checked_alt(alt or {})
    with transaction.atomic():
        if focal_x is not None:
            asset.focal_x = _checked_focal(focal_x)
        if focal_y is not None:
            asset.focal_y = _checked_focal(focal_y)
        _set_alt(asset, new_alt)
        asset.save()
        if references.used_by(ReferenceKind.MEDIA, asset.pk):
            _reaches_the_storefront(asset)
    return asset


def delete_asset(user: Any, asset: MediaAsset) -> None:
    require_permission(user, DELETE_PERMISSION)
    with transaction.atomic():
        used = references.used_by(ReferenceKind.MEDIA, asset.pk)
        if used:
            raise MediaInUse(used_by=used)
        name, storage = asset.file.name, asset.file.storage
        asset.delete()
        if name:
            transaction.on_commit(lambda: storage.delete(name))
