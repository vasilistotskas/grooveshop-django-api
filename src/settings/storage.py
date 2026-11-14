"""Static files, media and what an upload may be."""

from os import getenv, path

from settings.env import BASE_DIR, DEBUG, SRC_DIR, STATIC_BASE_URL

STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]

STATICFILES_DIRS = [
    path.join(SRC_DIR, "static"),
]

# ``MEDIA_URL`` is ABSOLUTE in every environment, local dev included:
# every ``ImageField``/``FileField`` DRF renders surfaces
# ``storage.url(name)`` to the frontend, drf-spectacular emits it with
# ``format: 'uri'`` and the storefront's Zod schema validates it with
# ``z.url()``, which rejects a relative path. ``/media/...`` failed every
# endpoint that exposes an uploaded image (PayWay icons, product images,
# shipping provider logos, ...) in dev. ``STATIC_BASE_URL`` defaults to
# ``http://localhost:8000``; a developer running Django on another port
# sets it in the environment.
MEDIA_URL = f"{STATIC_BASE_URL}/media/"

if DEBUG:
    STATIC_URL = "/static/"
    STATIC_ROOT = path.join(BASE_DIR, "staticfiles")
    MEDIA_ROOT = path.join(BASE_DIR, "mediafiles")
    _staticfiles_storage = (
        "django.contrib.staticfiles.storage.StaticFilesStorage"
    )
else:
    STATIC_URL = f"{STATIC_BASE_URL}/static/"
    STATIC_ROOT = path.join(BASE_DIR, "web", "staticfiles")
    MEDIA_ROOT = path.join(BASE_DIR, "web", "mediafiles")
    _staticfiles_storage = (
        "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
    )

STORAGES = {
    "default": {
        "BACKEND": "tenant.storage.TenantFileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": _staticfiles_storage,
    },
}

# The media library: what an upload may be. Raster formats only (an SVG can
# carry script, and has no size to reserve); names are Pillow's.
# Most pixels one uploaded image may have, whatever it is uploaded for
# (``core.uploads``: never above what the media service decodes). The byte
# caps are the media service's own, copied into ``core/data/image-limits.json``.
UPLOAD_IMAGE_MAX_PIXELS = int(getenv("UPLOAD_IMAGE_MAX_PIXELS", "40000000"))
# An upload over its byte cap or the pixel budget is scaled down to this long
# edge (twice the widest rendition the media service makes, 2048) and
# re-encoded at this quality, instead of refused (``core.image_normalise``).
UPLOAD_IMAGE_MAX_EDGE = int(getenv("UPLOAD_IMAGE_MAX_EDGE", "4096"))
UPLOAD_IMAGE_QUALITY = int(getenv("UPLOAD_IMAGE_QUALITY", "85"))

FILE_UPLOAD_MAX_MEMORY_SIZE = 2621440
