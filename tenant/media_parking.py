"""Take a suspended store's images out of the served path, and put them back.

A store's original uploads live in ``MEDIA_ROOT/{schema}``, which the static
file server publishes at ``/media/{schema}/…`` and media-stream fetches its
sources from. Flushing caches does not stop either from serving them, so
suspension moves the directory to ``MEDIA_ROOT/.suspended/{schema}``:

* the static file server never serves it: its nginx denies every path with a
  dot-segment (``location ~ /\\. { deny all; }``), which answers 403 for
  ``/media/.suspended/…`` in the encoded and traversal forms too;
* media-stream cannot ask for it: ``.suspended`` is not a valid schema;
* ``MEDIA_ROOT`` is the volume root, so the move is one ``rename``: atomic,
  instant, nothing copied.

Reactivation moves the directory back. Neither direction merges two trees:
when the destination already exists (files uploaded while suspended),
nothing moves and the conflict is logged for an operator, because
interleaving or overwriting a store's files is worse than serving a
placeholder a little longer.
"""

from __future__ import annotations

import logging
import os

from tenant.offboarding import tenant_media_dir, tenant_parked_media_dir

logger = logging.getLogger(__name__)


def park_tenant_media(schema_name: str) -> bool:
    """Move the store's uploads out of the served path; True if they moved."""
    return _move(
        tenant_media_dir(schema_name),
        tenant_parked_media_dir(schema_name),
        action="park",
        schema_name=schema_name,
    )


def restore_tenant_media(schema_name: str) -> bool:
    """Move parked uploads back into the served path; True if they moved."""
    return _move(
        tenant_parked_media_dir(schema_name),
        tenant_media_dir(schema_name),
        action="restore",
        schema_name=schema_name,
    )


def _move(
    source: str, destination: str, *, action: str, schema_name: str
) -> bool:
    """Rename ``source`` to ``destination``. Raises ``OSError`` if the rename fails."""
    if not os.path.isdir(source):
        return False
    if os.path.lexists(destination):
        logger.error(
            "Not moving the media of tenant %s (%s): %s already exists",
            schema_name,
            action,
            destination,
        )
        return False
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    os.rename(source, destination)
    logger.info(
        "Media of tenant %s: %s %s -> %s",
        schema_name,
        action,
        source,
        destination,
    )
    return True
