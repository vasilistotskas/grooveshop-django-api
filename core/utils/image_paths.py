"""Helpers for building tenant-aware image paths.

Domain models expose a ``main_image_path`` property that Nuxt + the
media-stream service consume to build cached, resized image URLs. The
path must match the media-stream route pattern — either the
``/media/{tenantSchema}/uploads/...`` tenant-scoped pattern or the
legacy ``/media/uploads/...`` pattern.

Historically every model hardcoded ``f"media/uploads/{subdir}/{basename}"``.
Under ``TenantFileSystemStorage`` files live at
``MEDIA_ROOT/{schema_name}/uploads/...`` and are served at
``MEDIA_URL{schema_name}/uploads/...``, so the hardcoded path 404s on
any non-public tenant.

``image_to_media_path()`` returns ``self.image.url`` with the leading
slash stripped — the same format media-stream expects and that the
Nuxt mediaStream image provider can feed directly into the URL
builder.
"""

from __future__ import annotations

from typing import Any


def image_to_media_path(image_field: Any) -> str:
    """Return the tenant-aware URL path for a stored image.

    Accepts any Django ImageField / FileField instance (or the None
    placeholder returned when the field is empty) and returns a path
    like ``media/webside/uploads/products/foo.jpg`` — no leading slash,
    no scheme, no host, so callers can concatenate it onto any origin.

    Returns an empty string when the field is unset or when the
    underlying storage cannot produce a URL (e.g. S3 with missing
    credentials in a CI run).
    """
    if not image_field or not getattr(image_field, "name", None):
        return ""
    try:
        url = image_field.url
    except (ValueError, AttributeError):
        return ""
    if not url:
        return ""
    return url.lstrip("/")
