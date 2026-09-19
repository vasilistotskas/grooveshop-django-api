"""Put the committed demo photographs into the tenant's own storage.

A fresh tenant has no media at all, and the demo's images used to be
ten files copied onto the volume by hand — which is why the catalogue
showed a powerbank for a USB-C cable: there was nothing else to point
at.

These come from ``devtools/demo_assets``, which is committed and built
by ``manage.py build_demo_assets``. Writing them goes through
``default_storage``, so inside a ``schema_context`` they land under the
tenant's own prefix (``TenantFileSystemStorage`` →
``MEDIA_ROOT/<schema>/…``) in every environment, with no kubectl step.

Idempotent by CONTENT, not by name: a file whose size already matches
the lock is left alone, so re-seeding does not rewrite the volume, and
a rebuilt asset does replace the old bytes.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urlparse

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent / "demo_assets"
LOCK_PATH = ASSETS_DIR / "manifest.lock.json"

#: Where each kind of asset lives inside the tenant's media prefix.
STORAGE_PREFIX = {
    "product": "uploads/products",
    "category": "uploads/categories",
    "blog": "uploads/blog",
    "hero": "uploads/pages",
}


class AssetMissing(RuntimeError):
    """A seed asked for an asset the repository does not carry."""


_lock_cache: dict[str, dict] | None = None


def _lock() -> dict[str, dict]:
    global _lock_cache
    if _lock_cache is None:
        if not LOCK_PATH.exists():
            raise AssetMissing(
                f"{LOCK_PATH} is missing — run `manage.py build_demo_assets`"
            )
        _lock_cache = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    return _lock_cache


def storage_name(key: str) -> str:
    """Where ``key`` lives inside the tenant's media, written or not."""
    entry = _lock().get(key)
    if entry is None:
        raise AssetMissing(f"Unknown demo asset {key!r}")
    return f"{STORAGE_PREFIX[entry['kind']]}/{key}.avif"


def ensure_asset(key: str) -> str:
    """Copy the committed asset into tenant storage; return its name.

    Call inside the tenant's ``schema_context``.
    """
    entry = _lock().get(key)
    if entry is None:
        raise AssetMissing(f"Unknown demo asset {key!r}")

    source = ASSETS_DIR / entry["kind"] / f"{key}.avif"
    if not source.exists():
        raise AssetMissing(
            f"{source} is missing — run `manage.py build_demo_assets`"
        )

    name = storage_name(key)
    if default_storage.exists(name):
        try:
            if default_storage.size(name) == entry["bytes"]:
                return name
        except OSError, NotImplementedError:
            # A backend that cannot size a file is a reason to rewrite,
            # not to guess it matches.
            pass
        # Delete first: FileSystemStorage renames around a collision, so
        # saving over an existing name would leave `<key>_A1b2c3.avif`
        # and the seeded path would point at the stale bytes.
        default_storage.delete(name)

    saved = default_storage.save(name, ContentFile(source.read_bytes()))
    if saved != name:
        logger.warning("Demo asset %s was stored as %s", name, saved)
    return saved


def ensure_assets(keys) -> dict[str, str]:
    """``ensure_asset`` for many keys, de-duplicated."""
    return {key: ensure_asset(key) for key in dict.fromkeys(keys)}


def media_path(key: str) -> str:
    """The path a SECTION PROP carries for this asset.

    A model with an ImageField gets this from ``image_to_media_path``,
    but a page section stores a bare string, so it needs the same shape
    built by hand: ``media/<schema>/uploads/...``, no leading slash and
    no host, which is what media-stream routes and what
    ``ImgWithFallback`` absolutises onto the tenant's assets origin.

    Writing a full URL here instead would hardcode a hostname into
    per-tenant data and break the moment the store moves domain.

    Call inside the tenant's ``schema_context``.
    """
    url = default_storage.url(storage_name(key))
    return urlparse(url).path.lstrip("/")
