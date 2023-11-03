from os import getenv
from os import path
from pathlib import Path

SYSTEM_ENV = getenv("SYSTEM_ENV", "dev")

BASE_DIR = Path(__file__).resolve().parent.parent

STATIC_URL = "/static/"
STATIC_ROOT = path.join(BASE_DIR, "staticfiles")
MEDIA_URL = "/media/"
MEDIA_ROOT = path.join(BASE_DIR, "mediafiles")
STATICFILES_DIRS = (path.join(BASE_DIR, "static"),)

if SYSTEM_ENV in ["dev", "GITHUB_WORKFLOW", "docker"]:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            # "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
else:
    AWS_S3_ACCESS_KEY_ID = getenv("AWS_S3_ACCESS_KEY_ID", "changeme")
    AWS_S3_SECRET_ACCESS_KEY = getenv("AWS_S3_SECRET_ACCESS_KEY", "changeme")
    AWS_STORAGE_BUCKET_NAME = getenv("AWS_STORAGE_BUCKET_NAME", "changeme")
    AWS_S3_REGION_NAME = getenv("AWS_S3_REGION_NAME", "changeme")
    AWS_S3_ENDPOINT_URL = f"https://{AWS_S3_REGION_NAME}.digitaloceanspaces.com"
    AWS_S3_OBJECT_PARAMETERS = {"CacheControl": "max-age=86400"}
    AWS_DEFAULT_ACL = "public-read"
    AWS_LOCATION = "static"
    AWS_MEDIA_LOCATION = "media"
    AWS_S3_CUSTOM_DOMAIN = getenv("AWS_S3_CUSTOM_DOMAIN", "changeme")
    DEFAULT_FILE_STORAGE = "core.storages.MediaRootS3BotoStorage"
    STORAGES = {
        "default": {
            "BACKEND": "core.storages.StaticRootS3BotoStorage",
        },
        "staticfiles": {
            "BACKEND": "storages.backends.s3boto3.S3StaticStorage",
        },
    }
