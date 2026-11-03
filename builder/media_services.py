"""Every write of the media library.

An upload is measured and checked here before it is stored, by the rule
every image upload shares (``core.uploads.validate_servable_image``: the
media service's per-format byte caps and pixel budget; only rasters here, an
SVG can carry script and its size is not a size). The size recorded is the
one a browser shows
(EXIF orientation applied), which is what the storefront reserves space
for.

A used asset cannot be deleted: the current release's reference index
(``builder.references``) names every document that points at it. Changing a
used asset's alt text or focal point reaches the storefront at once (cache
generation and the ``builder`` surface), because a release is immutable and
the media is resolved at render, not copied into it.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.uploadedfile import (
    SimpleUploadedFile,
    UploadedFile,
)
from django.db import connection, transaction
from django.utils.translation import gettext as _
from rest_framework import status

from builder import references
from builder.errors import BuilderError, StorefrontUnavailableError
from builder.models import MediaAsset, ReferenceKind
from builder.services import require_permission
from core import storefront
from core.cache.invalidation import mark_surface_stale
from core.uploads import image_limits, prepare_servable_image
from core.utils.i18n import available_language_codes
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


def _inspect(
    upload: UploadedFile,
) -> tuple[UploadedFile, str, tuple[int, int]]:
    """``(file to store, mime, displayed size)`` of an acceptable image, else
    refuse. The rule is the media service's (``core.uploads``), and a photo
    over its caps is shrunk rather than refused; a library image is a raster
    (an SVG can carry script, and its size is not a size)."""
    try:
        stored, image = prepare_servable_image(upload, allow_svg=False)
    except DjangoValidationError as exc:
        raise MediaInvalid(exc.messages[0], code=exc.code) from exc
    assert image.width is not None and image.height is not None
    return stored, image.mime, (image.width, image.height)


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


@dataclass(frozen=True, slots=True)
class Stored:
    """The asset an upload or an import ended with, and whether it is new:
    the same image twice is one asset, and the second time answers the
    first (its alt text and focal point are the first one's)."""

    asset: MediaAsset
    created: bool


def _digest(upload: UploadedFile) -> str:
    sha = hashlib.sha256()
    for chunk in upload.chunks():
        sha.update(chunk)
    upload.seek(0)
    return sha.hexdigest()


def _store(
    user: Any,
    upload: UploadedFile,
    *,
    focal_x: float | None,
    focal_y: float | None,
    alt: Mapping[str, Any] | None,
) -> Stored:
    upload, mime, (width, height) = _inspect(upload)
    digest = _digest(upload)
    known = MediaAsset.objects.filter(content_hash=digest).first()
    if known is not None:
        return Stored(known, created=False)
    asset = MediaAsset(
        file=upload,
        width=width,
        height=height,
        mime=mime,
        size_bytes=upload.size,
        content_hash=digest,
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
    return Stored(asset, created=True)


def create_asset(
    user: Any,
    *,
    upload: UploadedFile,
    focal_x: float | None = None,
    focal_y: float | None = None,
    alt: Mapping[str, Any] | None = None,
) -> Stored:
    require_permission(user, ADD_PERMISSION)
    return _store(user, upload, focal_x=focal_x, focal_y=focal_y, alt=alt)


#: A scheme (``https:``) or a protocol-relative start (``//host``).
_ABSOLUTE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*:|//)")
#: What no import path may hold: a query, a fragment, a backslash, a dot
#: segment, or any control character or space.
_UNSAFE = re.compile(r"[?#\\\s\x00-\x1f]|(^|/)\.\.?(/|$)")


def _import_path(url: str) -> str:
    """The absolute path *url* names on this store, else refuse.

    Only a path (``/img/x.png``, ``media/<store>/uploads/x.png``) is a source:
    anything with a scheme or a host is an address on the internet, which the
    server must not fetch for a staff member's sake (it could be an internal
    service) and would not own.
    """
    url = url.strip()
    if not url or len(url) > 1000:
        raise MediaInvalid(
            _("Name an image path of this store."), code="invalid_import_url"
        )
    if _ABSOLUTE.match(url):
        raise MediaInvalid(
            _("Only images of this store can be imported, not other sites."),
            code="external_url_not_allowed",
        )
    if _UNSAFE.search(url):
        raise MediaInvalid(
            _("That is not a valid image path."), code="invalid_import_url"
        )
    return "/" + url.lstrip("/")


def _too_large() -> MediaInvalid:
    return MediaInvalid(
        _("The file is larger than %(limit)s MB.")
        % {"limit": image_limits().largest_file_bytes // (1024 * 1024)},
        code="file_too_large",
    )


def _read_store_media(path: str) -> bytes:
    """The bytes of ``/media/<this store>/uploads/...``."""
    prefix = f"/media/{connection.schema_name}/"
    if not path.startswith(prefix):
        raise MediaInvalid(
            _("That image belongs to another store."),
            code="import_other_store",
        )
    name = path.removeprefix(prefix)
    if not name.startswith("uploads/"):
        raise MediaInvalid(
            _("That is not an uploaded image."), code="invalid_import_url"
        )
    storage = MediaAsset._meta.get_field("file").storage
    if not storage.exists(name):
        raise MediaInvalid(
            _("There is no such image."), code="import_not_found"
        )
    if storage.size(name) > image_limits().largest_file_bytes:
        raise _too_large()
    with storage.open(name, "rb") as handle:
        return handle.read()


def _fetch_storefront_static(path: str) -> bytes:
    """A file the storefront serves itself (``/img/...``), fetched from its
    internal address, never following a redirect: the answer must come from
    that one place."""
    try:
        base = storefront.base_url()
    except storefront.StorefrontUnavailable as exc:
        raise StorefrontUnavailableError() from exc
    url = f"{base}{path}"
    if urlsplit(url).netloc != urlsplit(base).netloc:
        raise MediaInvalid(
            _("That is not a valid image path."), code="invalid_import_url"
        )
    limit = image_limits().largest_file_bytes
    try:
        with requests.get(
            url,
            timeout=settings.BUILDER_STOREFRONT_TIMEOUT_SECONDS,
            allow_redirects=False,
            stream=True,
        ) as response:
            if 300 <= response.status_code < 400:
                raise MediaInvalid(
                    _("That address redirects, so it is not imported."),
                    code="import_redirect",
                )
            if response.status_code != 200:
                raise MediaInvalid(
                    _("There is no such image."), code="import_not_found"
                )
            body = bytearray()
            for chunk in response.iter_content(64 * 1024):
                body.extend(chunk)
                if len(body) > limit:
                    raise _too_large()
    except requests.RequestException as exc:
        raise StorefrontUnavailableError() from exc
    return bytes(body)


def import_url(
    user: Any,
    *,
    url: str,
    focal_x: float | None = None,
    focal_y: float | None = None,
    alt: Mapping[str, Any] | None = None,
) -> Stored:
    """Bring an image the store already shows into the library: a file in
    this store's own uploads (copied, so deleting the asset never touches
    the original) or one the storefront serves (``/img/...``). Both go
    through the checks of an upload. Nothing outside the store is fetched."""
    require_permission(user, ADD_PERMISSION)
    path = _import_path(url)
    data = (
        _read_store_media(path)
        if path.startswith("/media/")
        else _fetch_storefront_static(path)
    )
    upload = SimpleUploadedFile(path.rsplit("/", 1)[-1], data)
    return _store(user, upload, focal_x=focal_x, focal_y=focal_y, alt=alt)


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
