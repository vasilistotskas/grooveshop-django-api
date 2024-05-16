from os import getenv

from corsheaders.defaults import (
    default_headers,
)

APP_BASE_URL = getenv("APP_BASE_URL", "http://localhost:8000")
API_BASE_URL = getenv("API_BASE_URL", "http://localhost:8000")
NUXT_BASE_URL = getenv("NUXT_BASE_URL", "http://localhost:3000")
MEDIA_STREAM_BASE_URL = getenv("MEDIA_STREAM_BASE_URL", "http://localhost:3003")
AWS_STORAGE_BUCKET_NAME = getenv("AWS_STORAGE_BUCKET_NAME")
AWS_S3_CUSTOM_DOMAIN = f"{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com"

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
    f"https://{AWS_S3_CUSTOM_DOMAIN}",
    "http://backend-service:80",
    "http://frontend-nuxt-service:80",
    "http://media-stream-service:80",
    "http://localhost:1337",
]
CORS_ORIGIN_ALLOW_ALL = getenv("CORS_ORIGIN_ALLOW_ALL", "True") == "True"
CORS_ALLOW_ALL_ORIGINS = getenv("CORS_ALLOW_ALL_ORIGINS", "True") == "True"
CORS_ALLOW_CREDENTIALS = True
CORS_ORIGIN_WHITELIST = [
    APP_BASE_URL,
    API_BASE_URL,
    NUXT_BASE_URL,
    MEDIA_STREAM_BASE_URL,
    f"https://{AWS_S3_CUSTOM_DOMAIN}",
    "http://backend-service:80",
    "http://frontend-nuxt-service:80",
    "http://media-stream-service:80",
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
CORS_ALLOW_HEADERS = (
    *default_headers,
    "X-Session-Token",
    "location",
)
