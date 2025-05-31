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


class TinymceS3Storage(S3Boto3Storage):
    location = "media/uploads/tinymce"
    default_acl = "public-read"
    file_overwrite = False
