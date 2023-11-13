from os import getenv

APP_BASE_URL = getenv("APP_BASE_URL", "http://localhost:8000")
NGINX_BASE_URL = getenv("NGINX_BASE_URL", "http://localhost:8010")
NUXT_BASE_URL = getenv("NUXT_BASE_URL", "http://localhost:3000")
MEDIA_STREAM_BASE_URL = getenv("MEDIA_STREAM_BASE_URL", "http://localhost:3003")

CORS_EXPOSE_HEADERS = ["Content-Type", "X-CSRFToken"]
CORS_ALLOWED_ORIGINS = [
    APP_BASE_URL,
    NGINX_BASE_URL,
    NUXT_BASE_URL,
    MEDIA_STREAM_BASE_URL,
    "http://api.grooveshop.site",
    "http://grooveshop.site",
    "http://assets.grooveshop.site",
    "https://api.grooveshop.site",
    "https://grooveshop.site",
    "https://assets.grooveshop.site",
    "http://localhost:1337",
]
CORS_ORIGIN_ALLOW_ALL = getenv("CORS_ORIGIN_ALLOW_ALL", "True") == "True"
CORS_ALLOW_CREDENTIALS = True
CORS_ORIGIN_WHITELIST = [
    APP_BASE_URL,
    NGINX_BASE_URL,
    NUXT_BASE_URL,
    MEDIA_STREAM_BASE_URL,
    "http://api.grooveshop.site",
    "http://grooveshop.site",
    "http://assets.grooveshop.site",
    "https://api.grooveshop.site",
    "https://grooveshop.site",
    "https://assets.grooveshop.site",
    "http://localhost:1337",
]
CORS_ALLOW_METHODS = [
    "DELETE",
    "GET",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
]
