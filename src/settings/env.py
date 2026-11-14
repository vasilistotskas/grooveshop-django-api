"""Where this process runs and what it must not start without.

The paths, the ``.env`` load, the deployment (``SYSTEM_ENV``,
``PRODUCTION_PROFILE``), the secrets the production profile
requires, ``DEBUG`` and the hosts and base URLs."""

from os import getenv, path
from pathlib import Path

import django_stubs_ext
import dotenv
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

django_stubs_ext.monkeypatch()

# The source root: every app, this package, and the folders Django loads
# from the source (``core/templates``, ``locale``, ``locale_vendor``,
# ``static``). It is the directory on the Python path.
SRC_DIR = Path(__file__).resolve().parent.parent
# The repository root, and ``/home/app`` in the image: ``.env``,
# ``pyproject.toml`` and the folders written at run time (``logs``,
# ``backups``, ``mediafiles``, ``staticfiles``, ``web``).
BASE_DIR = SRC_DIR.parent

dotenv_file = BASE_DIR / ".env"


def load_dotenv_file():
    if path.isfile(dotenv_file):
        dotenv.load_dotenv(dotenv_file)


load_dotenv_file()

SYSTEM_ENV = getenv("SYSTEM_ENV", "dev")
# The deployment this is: ``production``, ``staging``, ``dev`` or ``ci``.
# ``SYSTEM_ENV`` names it (the admin's environment badge and tab prefix,
# admin/environment.py); PRODUCTION_PROFILE is the hardening a real
# deployment needs — required secrets, TLS to the database, JSON logs.
# Staging runs the production profile on purpose, so a release rehearses
# exactly what production will run, and still says it is staging.
PRODUCTION_PROFILE = SYSTEM_ENV in ("production", "staging")
# Whether this deployment's stores take real orders from real customers.
# Staging (mirrored rows, rehearsals), a developer machine and CI never
# sell; anything else, production or a SYSTEM_ENV this file does not
# know, is taken to, so a store there needs what a real sale requires (a
# recorded ΦΗΜ exemption before myDATA issues a receipt,
# ``order.mydata.config``). Listed as the exceptions, so an unknown name
# fails closed.
REAL_ORDERS_DEPLOYMENT = SYSTEM_ENV not in ("staging", "dev", "ci")

SECRET_KEY = getenv("SECRET_KEY", "")
if not SECRET_KEY:
    if PRODUCTION_PROFILE:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(
            "SECRET_KEY environment variable is required in production."
        )
    SECRET_KEY = "insecure-dev-key-do-not-use-in-production"

# Fernet keys that encrypt merchant secrets at rest
# (``core.db.encrypted_fields``), comma separated. The FIRST key encrypts
# and every key decrypts, so a rotation is: put the new key first, deploy,
# run ``rotate_tenant_secrets``, drop the old key. Never derived from
# SECRET_KEY. Generate one with ``Fernet.generate_key()``; the runbook is
# ``docs/secret-encryption.md``.
FIELD_ENCRYPTION_KEYS = [
    key.strip()
    for key in getenv("FIELD_ENCRYPTION_KEYS", "").split(",")
    if key.strip()
]
if not FIELD_ENCRYPTION_KEYS:
    if PRODUCTION_PROFILE:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEYS environment variable is required in "
            "production."
        )
    # Dev and CI only: lets a checkout boot and migrate without setup.
    # Data encrypted under it is not protected.
    FIELD_ENCRYPTION_KEYS = ["ZGV2LW9ubHktaW5zZWN1cmUta2V5LW5vdC1zZWNyZXQ="]
for _key in FIELD_ENCRYPTION_KEYS:
    try:
        Fernet(_key)
    except ValueError:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEYS holds an invalid Fernet key: each "
            "comma separated entry must be 32 url-safe base64-encoded "
            "bytes (Fernet.generate_key())."
        ) from None

# Key of the HMAC that turns an e-mail address into the hash the opt-out
# list stores (``emails.optout.address_hash``), so no row holds an address.
# Separate from SECRET_KEY on purpose: rotating SECRET_KEY must not orphan
# every opt-out, and changing THIS key forgets them all. Generate one with
# ``secrets.token_urlsafe(48)``; the runbook is ``docs/emails.md``.
EMAIL_HASH_KEY = getenv("EMAIL_HASH_KEY", "")
if not EMAIL_HASH_KEY:
    if PRODUCTION_PROFILE:
        raise ImproperlyConfigured(
            "EMAIL_HASH_KEY environment variable is required in production."
        )
    # Dev and CI only: lets a checkout boot without setup.
    EMAIL_HASH_KEY = "insecure-dev-email-hash-key-do-not-use-in-production"

_fallback = getenv("SECRET_KEY_FALLBACK", "")
SECRET_KEY_FALLBACKS = [_fallback] if _fallback else []

DEBUG = getenv("DEBUG", "False") == "True"

# Where this process runs: a Kubernetes pod, a container of the compose
# stack, a developer's machine. The log configuration and the beat
# schedule's development-only entry follow these.
IS_KUBERNETES = getenv("KUBERNETES_SERVICE_HOST") is not None
IS_DOCKER = path.exists("/.dockerenv")
IS_DEVELOPMENT = DEBUG or SYSTEM_ENV == "dev"

INTERNAL_IPS = [
    "127.0.0.1",
    "0.0.0.0",
]

if DEBUG:
    import socket

    hostname, aliaslist, ips = socket.gethostbyname_ex(socket.gethostname())
    INTERNAL_IPS = [ip[: ip.rfind(".")] + ".1" for ip in ips] + [
        "127.0.0.1",
        "10.0.2.2",
    ]

APP_MAIN_HOST_NAME = getenv("APP_MAIN_HOST_NAME", "localhost")
NUXT_BASE_URL = getenv("NUXT_BASE_URL", "http://localhost:3000")
MEDIA_STREAM_BASE_URL = getenv("MEDIA_STREAM_BASE_URL", "http://localhost:3003")
# Cluster-internal base URL for media-stream ADMIN calls (per-tenant
# cache flush on suspend/destroy). Defaults to the browser-facing base;
# in-cluster this should be the Service DNS (http://media-stream-service:80).
MEDIA_STREAM_INTERNAL_URL = getenv(
    "MEDIA_STREAM_INTERNAL_URL", MEDIA_STREAM_BASE_URL
)
# Shared secret for media-stream's InternalSecretGuard (x-internal-secret
# header). Must equal the media-stream INTERNAL_ADMIN_SECRET env value.
# Empty disables the flush call (fail-open — a suspended tenant's cached
# media then expires only by its TTL).
MEDIA_STREAM_INTERNAL_SECRET = getenv("MEDIA_STREAM_INTERNAL_SECRET", "")
STATIC_BASE_URL = getenv("STATIC_BASE_URL", "http://localhost:8000")
SITE_NAME = getenv("SITE_NAME", "Grooveshop")

# django-tenants rejects unregistered domains before Django's host
# validation, so ALLOWED_HOSTS=["*"] is safe. Dynamic tenant domains
# can't be statically listed. 'testserver' is not needed either —
# Django's setup_test_environment() adds it for test runs (G0357).
ALLOWED_HOSTS: list[str] = ["*"]

USE_X_FORWARDED_HOST = getenv("USE_X_FORWARDED_HOST", "True") == "True"

if PRODUCTION_PROFILE and not USE_X_FORWARDED_HOST:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "USE_X_FORWARDED_HOST must be True in production — the Nuxt proxy always "
        "sets the X-Forwarded-Host header and DRF pagination links rely on it."
    )

DEEPL_AUTH_KEY = getenv("DEEPL_AUTH_KEY", "changeme")
if DEEPL_AUTH_KEY == "changeme" and PRODUCTION_PROFILE:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "DEEPL_AUTH_KEY must be set in production "
        "(current value is the insecure default 'changeme')."
    )

APP_BASE_URL = getenv("APP_BASE_URL", "http://localhost:8000")
API_BASE_URL = getenv("API_BASE_URL", "http://localhost:8000")
