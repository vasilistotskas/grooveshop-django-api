from os import getenv
from os import path
from pathlib import Path

SYSTEM_ENV = getenv("SYSTEM_ENV", "dev")
DEVELOPMENT_MODE = getenv("DEVELOPMENT_MODE", "False") == "True"

BASE_DIR = Path(__file__).resolve().parent.parent

if DEVELOPMENT_MODE is True or SYSTEM_ENV == "GITHUB_WORKFLOW":
    STATIC_URL = "static/"
    STATIC_ROOT = path.join(BASE_DIR, "static")
    MEDIA_URL = "media/"
    MEDIA_ROOT = path.join(BASE_DIR, "media")
    STATICFILES_DIRS = (BASE_DIR.joinpath("files"),)
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
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
    STORAGES = {
        "default": {
            "BACKEND": "core.storages.CustomS3Boto3Storage",
        },
        "staticfiles": {
            "BACKEND": "storages.backends.s3boto3.S3StaticStorage",
        },
    }
