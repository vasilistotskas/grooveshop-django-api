from __future__ import annotations

import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import connection


class TenantFileSystemStorage(FileSystemStorage):
    """Scope uploaded media to MEDIA_ROOT/{schema_name}/."""

    @property
    def base_location(self):
        return os.path.join(settings.MEDIA_ROOT, connection.schema_name)

    @property
    def location(self):
        return self.base_location

    @property
    def base_url(self):
        return f"{settings.MEDIA_URL}{connection.schema_name}/"


def private_media_root() -> str:
    """The tree that is NOT served by any web server.

    ``PRIVATE_MEDIA_ROOT`` when set; otherwise ``MEDIA_ROOT`` with a
    ``_private`` suffix, which is what the compose file and the
    Kubernetes volume mount already provide as
    ``mediafiles_private/``. Never falls back to a path INSIDE
    ``MEDIA_ROOT``: a private file that lands under the public root is
    a public file.
    """
    base = getattr(settings, "PRIVATE_MEDIA_ROOT", None)
    if not base:
        base = (
            settings.MEDIA_ROOT + "_private"
            if getattr(settings, "MEDIA_ROOT", None)
            else "private_media"
        )
    return base


class TenantPrivateFileSystemStorage(FileSystemStorage):
    """Per-tenant PRIVATE storage; location resolves at ACCESS time.

    ``base_location`` / ``location`` are PROPERTIES, like the public
    storage above, so every read and write re-reads
    ``connection.schema_name`` and lands the file under
    ``{private_media_root()}/{schema}/``.

    Why they must be properties: a storage that captured the schema
    eagerly would freeze to whatever schema was active when the
    ``FileField`` first evaluated its storage — which is model-CLASS
    definition time, when no tenant is bound (i.e. ``public``) —
    sending every tenant's files into one directory.

    Never referenced directly from a ``FileField``: each caller passes
    a CALLABLE (``storage=_some_private_storage``) so the field's
    deconstructed storage reference, and therefore the migration
    state, does not name this class and can be re-pointed without a
    migration.

    It has no ``base_url`` on purpose. There is no URL: a file here is
    reachable only by streaming it through a view that authorises the
    reader (``OrderViewSet.invoice_download``,
    ``ContactAdmin.attachment_download_view``), which is what keeps
    the tree a defence-in-depth boundary rather than an obscure public
    path.
    """

    def _tenant_location(self) -> str:
        schema = getattr(connection, "schema_name", "public") or "public"
        return os.path.join(private_media_root(), schema)

    @property
    def base_location(self) -> str:
        return self._tenant_location()

    @property
    def location(self) -> str:
        return self._tenant_location()

    @property
    def base_url(self) -> None:
        """No public URL — and ``url()`` therefore RAISES.

        ``FileSystemStorage.base_url`` falls back to
        ``settings.MEDIA_URL``, so without this override every private
        file advertised a ``/media/<path>`` URL that no web server
        serves: a broken link today, and one line of configuration
        away from a real leak. Returning ``None`` is what makes
        ``FileSystemStorage.url`` raise ``ValueError`` instead —
        anything that reaches for a URL to one of these files is a
        bug, and it should say so loudly.
        """
        return None
