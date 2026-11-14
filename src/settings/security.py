"""CORS, CSRF, HTTPS, the session cookie and the edge proof."""

from os import getenv

from corsheaders.defaults import default_headers

from settings.env import (
    API_BASE_URL,
    APP_BASE_URL,
    DEBUG,
    MEDIA_STREAM_BASE_URL,
    NUXT_BASE_URL,
    STATIC_BASE_URL,
)

CORS_EXPOSE_HEADERS = [
    *default_headers,
    "X-Session-Token",
    "location",
]
CORS_ALLOWED_ORIGINS = [
    APP_BASE_URL,
    API_BASE_URL,
    NUXT_BASE_URL,
    MEDIA_STREAM_BASE_URL,
    STATIC_BASE_URL,
]
# The static list above is the PLATFORM's origins only. Every tenant
# storefront is a distinct origin that only its TenantDomain rows know;
# tenant.signals.allow_tenant_origin admits exactly those per request
# (same rule as TenantCsrfMiddleware). Never allow-all here: with
# credentials on, django-cors-headers echoes ANY Origin back.
CORS_ALLOW_CREDENTIALS = True
CORS_PREFLIGHT_MAX_AGE = 600
CORS_ALLOW_METHODS = [
    "DELETE",
    "GET",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
]
CORS_ALLOW_HEADERS = (
    *default_headers,
    "x-session-token",
    "x-cart-id",
    "idempotency-key",
    "location",
)

CSRF_USE_SESSIONS = False
CSRF_COOKIE_NAME = "csrftoken"
CSRF_COOKIE_AGE = 60 * 60 * 24 * 7 * 52  # 1 year
# No static cookie domain, and deliberately no env knob for one: the
# ``Domain`` of the session and CSRF cookies is derived from the request
# host on every response by ``tenant.middleware.TenantCookieDomainMiddleware``
# (``api.acme.com`` → ``.acme.com``), so each store's cookies stay inside
# its own registrable scope. A process-wide value could only ever be ONE
# store's apex — and it was (``.webside.gr``), until 2026-09-10.
CSRF_COOKIE_DOMAIN = None
CSRF_COOKIE_PATH = "/"
CSRF_COOKIE_SECURE = (
    not DEBUG
)  # Only send CSRF cookie over HTTPS when DEBUG is False
CSRF_COOKIE_HTTPONLY = (
    False  # Set to True to prevent JavaScript from reading the CSRF
)
CSRF_COOKIE_SAMESITE = "Lax"  # 'Lax' or 'None'. Use 'None' only if necessary and ensure CSRF_COOKIE_SECURE is True
CSRF_HEADER_NAME = "HTTP_X_CSRFTOKEN"

# CSRF_TRUSTED_ORIGINS should include only the domains that are trusted to send POST requests to your application
CSRF_TRUSTED_ORIGINS = [
    APP_BASE_URL.replace("http://", "https://"),  # Force HTTPS
    API_BASE_URL.replace("http://", "https://"),  # Force HTTPS
    NUXT_BASE_URL.replace("http://", "https://"),  # Force HTTPS
    MEDIA_STREAM_BASE_URL.replace("http://", "https://"),  # Force HTTPS
    STATIC_BASE_URL.replace("http://", "https://"),  # Force HTTPS
]

# Internal K8s service URLs and local dev origins are only valid in non-production
if DEBUG:
    CSRF_TRUSTED_ORIGINS.extend(
        [
            "http://backend-service:80",
            "http://frontend-nuxt-service:80",
            "http://media-stream-service:80",
            "http://localhost:3000",
        ]
    )

# Security Settings
SECURE_SSL_REDIRECT = (
    getenv("SECURE_SSL_REDIRECT", "False" if DEBUG else "True") == "True"
)
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https") if not DEBUG else None
)
_default_hsts = 0 if DEBUG else 31536000
SECURE_HSTS_SECONDS = int(getenv("SECURE_HSTS_SECONDS", str(_default_hsts)))
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SECURE_HSTS_PRELOAD = SECURE_HSTS_SECONDS > 0

SESSION_CACHE_ALIAS = "default"
SESSION_COOKIE_NAME = "sessionid"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 7 * 2
# See CSRF_COOKIE_DOMAIN: per-request, never a static apex.
SESSION_COOKIE_DOMAIN = None
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_PATH = "/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_SAVE_EVERY_REQUEST = False
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_SERIALIZER = "django.contrib.sessions.serializers.JSONSerializer"

# Shared secret stamped onto every request by a Cloudflare Transform Rule
# (``X-Origin-Verify``). It is the ONLY thing that distinguishes traffic
# that came through our edge from traffic sent straight to the origin's
# node IPs, which stay publicly reachable. ``core.client_ip`` uses it to
# decide whether Cloudflare's client-IP headers can be believed; see that
# module for why the rightmost X-Forwarded-For hop is an internal address
# here. Empty by default, which simply disables the trust and leaves the
# previous (coarse but unspoofable) behaviour in place.
ORIGIN_VERIFY_SECRET = getenv("ORIGIN_VERIFY_SECRET", "")
