from os import getenv

DEBUG = getenv("DEBUG", "True") == "True"

APP_BASE_URL = getenv("APP_BASE_URL", "http://localhost:8000")
NGINX_BASE_URL = getenv("NGINX_BASE_URL", "http://localhost:8010")
NUXT_BASE_URL = getenv("NUXT_BASE_URL", "http://localhost:3000")
MEDIA_STREAM_BASE_URL = getenv("MEDIA_STREAM_BASE_URL", "http://localhost:3003")

CSRF_COOKIE_NAME = "csrftoken"
CSRF_COOKIE_AGE = 60 * 60 * 24 * 7 * 52
CSRF_COOKIE_DOMAIN = None
CSRF_COOKIE_PATH = "/"
CSRF_COOKIE_SECURE = False if DEBUG else True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_HEADER_NAME = "HTTP_X_CSRFTOKEN"
CSRF_TRUSTED_ORIGINS = [
    APP_BASE_URL,
    NGINX_BASE_URL,
    NUXT_BASE_URL,
    MEDIA_STREAM_BASE_URL,
    "http://localhost:1337",
]
CSRF_USE_SESSIONS = False
