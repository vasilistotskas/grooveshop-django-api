import os

from django.core.files.storage import storages
from storages.backends.s3boto3 import S3Boto3Storage


class StaticStorage(S3Boto3Storage):
    location = "static"
    default_acl = "public-read"

    def __init__(self, **settings):
        super().__init__(**settings)
        self.local_storage = storages.create_storage(
            {"BACKEND": "compressor.storage.CompressorFileStorage"}
        )

    def save(self, name, content, max_length=None):
        self.local_storage.save(name, content)
        super().save(name, self.local_storage._open(name))
        return name


class PublicMediaStorage(S3Boto3Storage):
    location = "media"
    default_acl = "public-read"
    file_overwrite = False


class PrivateMediaStorage(S3Boto3Storage):
    location = "private"
    default_acl = "private"
    file_overwrite = False
    custom_domain = False
    # ``AWS_QUERYSTRING_AUTH`` is ``False`` globally (static assets and
    # public media live behind a CDN and never need signing). Private
    # files MUST be signed — without this override ``.url()`` returns a
    # bare S3 URL that 403s on anyone without IAM credentials, which
    # silently broke the customer-facing invoice download. Override
    # locally so any direct-storage consumer gets a presigned URL.
    querystring_auth = True


class TinymceS3Storage(S3Boto3Storage):
    location = "media/uploads/tinymce"
    default_acl = "public-read"
    file_overwrite = False


# ---------------------------------------------------------------------------
# Tenant-scoped S3 storage classes
# ---------------------------------------------------------------------------
# These subclasses isolate each tenant's uploaded media under a per-schema
# S3 key prefix so that tenants sharing one S3 bucket cannot read each
# other's files by guessing keys.
#
# Key format:
#   Public  : media/{schema_name}/...
#   Private : media/{schema_name}/private/...
#
# MIGRATION NOTE (webside.gr):
#   Existing webside files live at the legacy flat location:
#     s3://bucket/media/uploads/...  (no tenant prefix)
#   Before switching USE_AWS=True in production run:
#     aws s3 mv s3://bucket/media/ s3://bucket/media/webside/ --recursive \
#       --exclude "webside/*"
#   OR set STORAGE_LEGACY_FALLBACK=True and the storage class will proxy
#   missing tenant-scoped keys back to the legacy path.  Remove the
#   fallback once the S3 mv is complete.
# ---------------------------------------------------------------------------

_LEGACY_FALLBACK = os.getenv("STORAGE_LEGACY_FALLBACK", "False") == "True"


class _TenantLocationMixin:
    """Mixin that computes the S3 ``location`` from the active tenant schema.

    Uses a property so the value is read at call time — storage instances
    may be long-lived (module-level singletons in django-storages 1.14+)
    and the schema_name must reflect the *current* request, not the one
    that first instantiated the class.
    """

    #: Suffix appended after the schema name, e.g. "" or "/private".
    _tenant_suffix: str = ""

    @property
    def location(self) -> str:  # type: ignore[override]
        from django.db import connection

        schema_name = getattr(connection, "schema_name", None) or "public"
        return f"media/{schema_name}{self._tenant_suffix}"

    def _open(self, name, mode="rb"):
        """Try tenant-scoped path; fall back to legacy flat path if enabled."""
        try:
            return super()._open(name, mode)  # type: ignore[misc]
        except Exception:
            if not _LEGACY_FALLBACK:
                raise
            # Attempt to read from legacy location (no tenant prefix).
            # We temporarily swap location to the legacy root, open the
            # file, then restore — all within this call so there is no
            # concurrency hazard.
            original_location = self.__dict__.get("_legacy_opened")
            if original_location:
                raise  # prevent infinite recursion
            self.__dict__["_legacy_opened"] = True
            try:
                from storages.backends.s3boto3 import S3Boto3Storage  # noqa: PLC0415

                legacy = S3Boto3Storage(
                    location="media",
                    default_acl=getattr(self, "default_acl", None),
                    file_overwrite=getattr(self, "file_overwrite", False),
                )
                return legacy._open(name, mode)
            finally:
                self.__dict__.pop("_legacy_opened", None)


class TenantPublicMediaStorage(_TenantLocationMixin, S3Boto3Storage):
    """S3 public media storage scoped to the current tenant's schema."""

    _tenant_suffix = ""
    default_acl = "public-read"
    file_overwrite = False


class TenantPrivateMediaStorage(_TenantLocationMixin, S3Boto3Storage):
    """S3 private media storage scoped to the current tenant's schema."""

    _tenant_suffix = "/private"
    default_acl = "private"
    file_overwrite = False
    custom_domain = False
    querystring_auth = True
