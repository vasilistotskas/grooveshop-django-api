"""Where a contact attachment's bytes live, and under what name.

The whole security posture of this feature rests on two facts about
this module, so they are stated here rather than left to be inferred:

1. The tree is PRIVATE. ``TenantPrivateFileSystemStorage`` writes
   under ``{private_media_root()}/{schema}/``, which no web server
   serves and which has no ``base_url`` (asking one of these files
   for a URL RAISES). An attachment is reachable only by streaming it
   through ``ContactAdmin.attachment_download_view``, which authorises
   the reader first. That is what stops an anonymous upload endpoint
   from becoming a public file host on the merchant's own domain.

2. The stored path never contains the uploader's filename. The name a
   visitor chose is DATA (``ContactAttachment.original_name``, shown
   in the admin and used for the download's ``Content-Disposition``);
   the path on disk is derived from the row's own UUID. A filename is
   attacker-controlled input — path traversal, a 4KB name, a name that
   is valid UTF-8 but not valid on the filesystem, a name that
   collides with another visitor's — and none of that can matter if it
   never reaches the filesystem.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from tenant.storage import TenantPrivateFileSystemStorage

if TYPE_CHECKING:  # pragma: no cover
    from contact.models import ContactAttachment


def private_attachment_storage() -> Any:
    """The storage backend for ``ContactAttachment.file``.

    A CALLABLE, not an instance: ``FileField`` evaluates it once at
    model-class definition time, when no tenant is bound, so returning
    an eagerly-located storage would pin every tenant's attachments to
    the ``public`` schema for the process lifetime. Returning the class
    (whose location is a property) defers the per-tenant partitioning
    to each file operation instead. It also keeps the field's
    deconstructed storage reference — and therefore the migration
    state — pointing at this function rather than at a storage class,
    so the backend can be re-pointed without a migration.
    """
    return TenantPrivateFileSystemStorage()


def attachment_upload_to(instance: ContactAttachment, filename: str) -> str:
    """``contact/{YYYY}/{MM}/{uuid}{ext}`` — never the uploader's name.

    The month prefix keeps the retention sweep and any archival
    operating on a stable, cheap-to-enumerate prefix instead of a
    single directory that grows forever.

    The extension is taken from the SNIFFED content type rather than
    from ``filename``, because the extension a visitor sends is a
    claim and the magic bytes are evidence. It is only cosmetic here
    (nothing on the server ever dispatches on it) but it makes the
    private tree readable to a human operator.
    """
    from django.utils import timezone

    stamp = instance.created_at or timezone.now()
    suffix = instance.extension()
    return os.path.join(
        "contact",
        f"{stamp:%Y}",
        f"{stamp:%m}",
        f"{instance.uuid}{suffix}",
    )
