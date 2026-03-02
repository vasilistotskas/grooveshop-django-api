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
