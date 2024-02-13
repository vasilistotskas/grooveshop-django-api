from os import getenv
from os import path
from pathlib import Path

SYSTEM_ENV = getenv("SYSTEM_ENV", "dev")

BASE_DIR = Path(__file__).resolve().parent.parent

STATICFILES_DIRS = (path.join(BASE_DIR, "static"),)

DATA_UPLOAD_MAX_MEMORY_SIZE = 5242880

if SYSTEM_ENV in ["dev", "ci", "docker"]:
    STATIC_URL = "/static/"
    STATIC_ROOT = path.join(BASE_DIR, "staticfiles")
    MEDIA_URL = "/media/"
    MEDIA_ROOT = path.join(BASE_DIR, "mediafiles")
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
    # aws settings
    AWS_ACCESS_KEY_ID = getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = getenv("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = getenv("AWS_STORAGE_BUCKET_NAME")
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = False
    AWS_S3_CUSTOM_DOMAIN = f"{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com"
    AWS_S3_OBJECT_PARAMETERS = {"CacheControl": "max-age=86400"}
    # s3 static settings
    STATIC_LOCATION = "static"
    STATIC_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/{STATIC_LOCATION}/"
    # s3 public media settings
    PUBLIC_MEDIA_LOCATION = "media"
    MEDIA_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/{PUBLIC_MEDIA_LOCATION}/"
    # s3 private media settings
    PRIVATE_MEDIA_LOCATION = "private"
    PRIVATE_FILE_STORAGE = "core.storages.PrivateMediaStorage"
    STORAGES = {
        "default": {
            "BACKEND": "core.storages.PublicMediaStorage",
        },
        "staticfiles": {
            "BACKEND": "core.storages.StaticStorage",
        },
    }
    TINYMCE_JS_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/tinymce/tinymce.min.js"
    TINYMCE_JS_ROOT = f"https://{AWS_S3_CUSTOM_DOMAIN}/tinymce/"
