from os import getenv

DEBUG = getenv("DEBUG", "True") == "True"

APP_BASE_URL = getenv("APP_BASE_URL", "http://localhost:8000")
API_BASE_URL = getenv("API_BASE_URL", "http://localhost:8000")
NUXT_BASE_URL = getenv("NUXT_BASE_URL", "http://localhost:3000")
MEDIA_STREAM_BASE_URL = getenv("MEDIA_STREAM_BASE_URL", "http://localhost:3003")
AWS_STORAGE_BUCKET_NAME = getenv("AWS_STORAGE_BUCKET_NAME")
AWS_S3_CUSTOM_DOMAIN = f"{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com"

CSRF_COOKIE_NAME = "csrftoken"
CSRF_COOKIE_AGE = 60 * 60 * 24 * 7 * 52  # 1 year
CSRF_COOKIE_DOMAIN = getenv("CSRF_COOKIE_DOMAIN", ".localhost")
CSRF_COOKIE_PATH = "/"
CSRF_COOKIE_SECURE = not DEBUG  # Only send CSRF cookie over HTTPS when DEBUG is False
CSRF_COOKIE_HTTPONLY = True  # Helps mitigate XSS attacks
CSRF_COOKIE_SAMESITE = "Lax"  # 'Lax' or 'None'. Use 'None' only if necessary and ensure CSRF_COOKIE_SECURE is True
CSRF_HEADER_NAME = "HTTP_X_CSRFTOKEN"

# CSRF_TRUSTED_ORIGINS should include only the domains that are trusted to send POST requests to your application
CSRF_TRUSTED_ORIGINS = [
    APP_BASE_URL.replace("http://", "https://"),  # Force HTTPS
    API_BASE_URL.replace("http://", "https://"),  # Force HTTPS
    NUXT_BASE_URL.replace("http://", "https://"),  # Force HTTPS
    MEDIA_STREAM_BASE_URL.replace("http://", "https://"),  # Force HTTPS
    f"https://{AWS_S3_CUSTOM_DOMAIN}",
    "http://backend-service:80",
    "http://frontend-nuxt-service:80",
    "http://media-stream-service:80",
]

# Local development URLs should only be in the list if in DEBUG mode
if DEBUG:
    CSRF_TRUSTED_ORIGINS.extend(
        [
            "http://localhost:3000",
        ]
    )

CSRF_USE_SESSIONS = (
    False  # Default is False, use True to store CSRF token in the session
)
