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
