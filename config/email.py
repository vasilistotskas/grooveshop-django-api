from os import getenv

EMAIL_BACKEND = getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = getenv("EMAIL_HOST", "localhost")
EMAIL_PORT = getenv("EMAIL_PORT", "25")
EMAIL_HOST_USER = getenv("EMAIL_HOST_USER", "localhost@gmail.com")
EMAIL_HOST_PASSWORD = getenv("EMAIL_HOST_PASSWORD", "changeme")
EMAIL_USE_TLS = getenv("EMAIL_USE_TLS", "False") == "True"
DEFAULT_FROM_EMAIL = getenv("DEFAULT_FROM_EMAIL", "localhost@gmail.com")
ADMIN_EMAIL = getenv("ADMIN_EMAIL", "localhost@gmail.com")
INFO_EMAIL = getenv("INFO_EMAIL", "localhost@gmail.com")
