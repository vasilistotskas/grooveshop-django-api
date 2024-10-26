from datetime import datetime
from datetime import timedelta
from os import getenv
from os import makedirs
from os import path
from pathlib import Path

import dotenv
from celery.schedules import crontab
from corsheaders.defaults import (
    default_headers,
)
from django.utils.translation import gettext_lazy as _

BASE_DIR = Path(__file__).resolve().parent

dotenv_file = BASE_DIR / ".env"


def load_dotenv_file():
    if path.isfile(dotenv_file):
        dotenv.load_dotenv(dotenv_file)


load_dotenv_file()

SYSTEM_ENV = getenv("SYSTEM_ENV", "dev")

SECRET_KEY = getenv("SECRET_KEY", "changeme")

DEBUG = getenv("DEBUG", "True") == "True"

DJANGO_ADMIN_FORCE_ALLAUTH = getenv("DJANGO_ADMIN_FORCE_ALLAUTH", "True") == "True"

INTERNAL_IPS = [
    "127.0.0.1",
    "0.0.0.0",
]

DATA_UPLOAD_MAX_NUMBER_FIELDS = 22500

SERIALIZATION_MODULES = {"json": "djmoney.serializers"}

if DEBUG:
    import socket  # only if you haven't already imported this

    hostname, aliaslist, ips = socket.gethostbyname_ex(socket.gethostname())
    INTERNAL_IPS = [ip[: ip.rfind(".")] + ".1" for ip in ips] + [
        "127.0.0.1",
        "10.0.2.2",
    ]

APP_MAIN_HOST_NAME = getenv("APP_MAIN_HOST_NAME", "localhost")
NUXT_BASE_URL = getenv("NUXT_BASE_URL", "http://localhost:3000")
NUXT_BASE_DOMAIN = getenv("NUXT_BASE_DOMAIN", "localhost:3000")
MEDIA_STREAM_BASE_URL = getenv("MEDIA_STREAM_BASE_URL", "http://localhost:3003")
STATIC_BASE_URL = getenv("STATIC_BASE_URL", "http://localhost:3000")

ALLOWED_HOSTS = []  # Start with an empty list

# Add any additional hosts from the environment variable
additional_hosts = getenv("ALLOWED_HOSTS", "*").split(",")
ALLOWED_HOSTS.extend(filter(None, additional_hosts))  # Filter out empty strings

USE_X_FORWARDED_HOST = getenv("USE_X_FORWARDED_HOST", "False") == "True"

# Django built-in apps
DJANGO_APPS = [
    "admin.apps.MyAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.postgres",
]

# Project-specific apps
LOCAL_APPS = [
    "core",
    "user",
    "product",
    "order",
    "search",
    "slider",
    "blog",
    "seo",
    "tip",
    "vat",
    "country",
    "region",
    "pay_way",
    "session",
    "cart",
    "notification",
    "authentication",
    "contact",
    "tag",
    "meili",
]

# Third-party apps
THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework.authtoken",
    "corsheaders",
    "mptt",
    "tinymce",
    "rosetta",
    "parler",
    "storages",
    "django_filters",
    "drf_spectacular",
    "djmoney",
    "phonenumber_field",
    "allauth",
    "allauth.account",
    "allauth.headless",
    "allauth.socialaccount",
    "allauth.mfa",
    "allauth.usersessions",
    "allauth.socialaccount.providers.facebook",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.discord",
    "allauth.socialaccount.providers.github",
    "django_celery_beat",
    "django_celery_results",
    "pytest",
    "pytest_django",
    "dbbackup",
    "extra_settings",
    "knox",
    "simple_history",
]

# Combine all apps together for the INSTALLED_APPS setting
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Reset login flow middleware. If this middleware is included, the login
    # flow is reset if another page is loaded between login and successfully
    # entering two-factor credentials.
    "allauth.account.middleware.AccountMiddleware",
    "allauth.usersessions.middleware.UserSessionsMiddleware",
    "djangorestframework_camel_case.middleware.CamelCaseMiddleWare",
    "simple_history.middleware.HistoryRequestMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [path.join(BASE_DIR, "core/templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.metadata",
            ],
        },
    },
]

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

WSGI_APPLICATION = "wsgi.application"

# User Model
AUTH_USER_MODEL = "user.UserAccount"

# Internationalization
LANGUAGE_CODE = getenv("LANGUAGE_CODE", "el")
TIME_ZONE = getenv("TIME_ZONE", "Europe/Athens")
USE_I18N = getenv("USE_I18N", "True") == "True"
USE_TZ = getenv("USE_TZ", "True") == "True"

# Site info
SITE_ID = int(getenv("SITE_ID", 1))

LANGUAGES = [
    ("el", _("Greek")),
    ("en", _("English")),
    ("de", _("German")),
]

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Rest Framework
REST_FRAMEWORK = {
    # Authentication
    "DEFAULT_AUTHENTICATION_CLASSES": ("knox.auth.TokenAuthentication",),
    # Permissions
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    # Throttling
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": None if DEBUG else "50000/day",
        "user": None if DEBUG else "150000/day",
        "burst": None if DEBUG else "5/minute",
    },
    # Filtering
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    # Schema
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Pagination
    "DEFAULT_PAGINATION_CLASS": "core.pagination.page_number.PageNumberPaginator",
    "PAGE_SIZE": 52,
    # Renderers and parsers
    "DEFAULT_RENDERER_CLASSES": (
        "djangorestframework_camel_case.render.CamelCaseJSONRenderer",
        "djangorestframework_camel_case.render.CamelCaseBrowsableAPIRenderer",
        # Any other renders
    ),
    "DEFAULT_PARSER_CLASSES": (
        # If you use MultiPartFormParser or FormParser, we also have a camel case version
        "djangorestframework_camel_case.parser.CamelCaseFormParser",
        "djangorestframework_camel_case.parser.CamelCaseMultiPartParser",
        "djangorestframework_camel_case.parser.CamelCaseJSONParser",
        # Any other parsers
    ),
    # Metadata
    "DEFAULT_METADATA_CLASS": "rest_framework.metadata.SimpleMetadata",
    # Other
    "COERCE_DECIMAL_TO_STRING": False,
}

# Other general settings
APPEND_SLASH = getenv("APPEND_SLASH", "False") == "True"

DEEPL_AUTH_KEY = getenv("DEEPL_AUTH_KEY", "changeme")

LOCALE_PATHS = [path.join(BASE_DIR, "locale/")]

ENABLE_DEBUG_TOOLBAR = getenv("ENABLE_DEBUG_TOOLBAR", "False") == "True"

ADMINS = [
    ("Admin", getenv("ADMIN_EMAIL", "")),
    ("Info", getenv("INFO_EMAIL", "")),
]

if ENABLE_DEBUG_TOOLBAR:
    INSTALLED_APPS += ["debug_toolbar"]
    MIDDLEWARE.append("debug_toolbar.middleware.DebugToolbarMiddleware")

    DEBUG_TOOLBAR_PANELS = [
        "debug_toolbar.panels.history.HistoryPanel",
        "debug_toolbar.panels.versions.VersionsPanel",
        "debug_toolbar.panels.timer.TimerPanel",
        "debug_toolbar.panels.settings.SettingsPanel",
        "debug_toolbar.panels.headers.HeadersPanel",
        "debug_toolbar.panels.staticfiles.StaticFilesPanel",
        "debug_toolbar.panels.templates.TemplatesPanel",
        "debug_toolbar.panels.cache.CachePanel",
        "debug_toolbar.panels.signals.SignalsPanel",
        "debug_toolbar.panels.redirects.RedirectsPanel",
        "debug_toolbar.panels.profiling.ProfilingPanel",
    ]
    DEBUG_TOOLBAR_CONFIG = {
        "INTERCEPT_REDIRECTS": False,
        "RESULTS_CACHE_SIZE": 100,
    }

SOCIALACCOUNT_ADAPTER = "user.adapter.SocialAccountAdapter"
SOCIALACCOUNT_STORE_TOKENS = True
SOCIALACCOUNT_QUERY_EMAIL = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_VERIFICATION = "mandatory"
SOCIALACCOUNT_REQUESTS_TIMEOUT = 10
SOCIALACCOUNT_PROVIDERS = {
    "github": {
        "APP": {
            "client_id": getenv("SOCIALACCOUNT_GITHUB_CLIENT_ID", ""),
            "secret": getenv("SOCIALACCOUNT_GITHUB_SECRET", ""),
            "key": "",
        },
        "SCOPE": ["read:user", "user:email" "repo"],
        "VERIFIED_EMAIL": True,
    },
    "google": {
        "APP": {
            "client_id": getenv("SOCIALACCOUNT_GOOGLE_CLIENT_ID", ""),
            "secret": getenv("SOCIALACCOUNT_GOOGLE_SECRET", ""),
            "key": "",
        },
        "SCOPE": ["profile", "email", "openid"],
        "AUTH_PARAMS": {"access_type": "offline" if DEBUG else "online"},
        "VERIFIED_EMAIL": True,
    },
    "discord": {
        "APP": {
            "client_id": getenv("SOCIALACCOUNT_DISCORD_CLIENT_ID", ""),
            "secret": getenv("SOCIALACCOUNT_DISCORD_SECRET", ""),
            "key": getenv("SOCIALACCOUNT_DISCORD_PUBLIC_KEY", ""),
        },
        "SCOPE": ["email", "identify"],
        "VERIFIED_EMAIL": True,
    },
    "facebook": {
        "APP": {
            "client_id": getenv("SOCIALACCOUNT_FACEBOOK_CLIENT_ID", ""),
            "secret": getenv("SOCIALACCOUNT_FACEBOOK_SECRET", ""),
        },
        "METHOD": "oauth2",
        "SCOPE": [
            "email",
            "public_profile",
        ],
        "VERSION": "v20.0",
        "GRAPH_API_URL": "https://graph.facebook.com/v20.0",
        "FIELDS": [
            "id",
            "first_name",
            "last_name",
            "middle_name",
            "name",
            "name_format",
            "picture",
            "short_name",
        ],
        "VERIFIED_EMAIL": True,
    },
}
SOCIALACCOUNT_FORMS = {
    "disconnect": "allauth.socialaccount.forms.DisconnectForm",
    "signup": "allauth.socialaccount.forms.SignupForm",
}

ACCOUNT_CHANGE_EMAIL = True if DEBUG else False
ACCOUNT_USER_MODEL_USERNAME_FIELD = "username"
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_NOTIFICATIONS = True
ACCOUNT_USERNAME_MIN_LENGTH = 2
ACCOUNT_USERNAME_MAX_LENGTH = 30
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_AUTHENTICATION_METHOD = "username_email"
ACCOUNT_ADAPTER = "user.adapter.UserAccountAdapter"
ACCOUNT_SIGNUP_REDIRECT_URL = NUXT_BASE_URL + "/account"
ACCOUNT_LOGIN_BY_CODE_ENABLED = True
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = True
ACCOUNT_EMAIL_VERIFICIATION = "mandatory"
ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS = 3
ACCOUNT_LOGIN_BY_CODE_TIMEOUT = 300
ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD = "email_confirm"
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "http" if DEBUG else "https"
ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE = False

LOGIN_REDIRECT_URL = NUXT_BASE_URL + "/account"
USERSESSIONS_TRACK_ACTIVITY = True

HEADLESS_ONLY = True
HEADLESS_TOKEN_STRATEGY = "core.api.tokens.SessionTokenStrategy"
HEADLESS_FRONTEND_URLS = {
    "account_confirm_email": f"{NUXT_BASE_URL}/account/verify-email/{{key}}",
    "account_reset_password": f"{NUXT_BASE_URL}/account/password/reset",
    "account_reset_password_from_key": f"{NUXT_BASE_URL}/account/password/reset/key/{{key}}",
    "account_signup": f"{NUXT_BASE_URL}/account/registration",
    "socialaccount_login_error": f"{NUXT_BASE_URL}/account/provider/callback",
}

USE_AWS = getenv("USE_AWS", "False") == "True"
REDIS_HOST = getenv("REDIS_HOST", "localhost")
REDIS_PORT = getenv("REDIS_PORT", "6379")
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
DISABLE_CACHE = getenv("DISABLE_CACHE", "False").lower() == "true"
DEFAULT_CACHE_TTL = int(getenv("DEFAULT_CACHE_TTL", 60 * 60 * 2))

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "redis",
    },
}

if SYSTEM_ENV == "ci":
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": "redis://127.0.0.1:6379/0",
            "KEY_PREFIX": "redis",
        },
    }


CELERY_BROKER_URL = getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = getenv("CELERY_RESULT_BACKEND", "django-db")
CELERY_CACHE_BACKEND = getenv("CELERY_CACHE_BACKEND", "django-cache")
CELERY_TASK_TRACK_STARTED = True
CELERY_ENABLE_UTC = False
CELERY_BROKER_HEARTBEAT = 0
CELERY_BROKER_POOL_LIMIT = None
CELERY_BROKER_TRANSPORT_OPTIONS = {"confirm_publish": True}
CELERY_BROKER_CONNECTION_TIMEOUT = 30
CELERY_BROKER_CONNECTION_RETRY = True
CELERY_BROKER_CONNECTION_MAX_RETRIES = 100
CELERY_RESULT_BACKEND_ALWAYS_RETRY = True
CELERY_RESULT_BACKEND_MAX_RETRIES = 10
CELERY_TIMEZONE = getenv("TIME_ZONE", "Europe/Athens")
CELERY_ACCEPT_CONTENT = ["application/json"]
CELERY_RESULT_SERIALIZER = "json"
CELERY_TASK_SERIALIZER = "json"
CELERY_TASK_TIME_LIMIT = 30 * 60
CELERY_TASK_SOFT_TIME_LIMIT = 60
CELERY_RESULT_EXTENDED = True
CELERY_TASK_RESULT_EXPIRES = 3600
CELERY_WORKER_SEND_TASK_EVENTS = True
CELERY_TASK_SEND_SENT_EVENT = True
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_EAGER_PROPAGATES = False

CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

CELERY_BEAT_SCHEDULE = {
    "send-inactive-user-notifications": {
        "task": "core.tasks.send_inactive_user_notifications",
        "schedule": crontab(hour="6", minute="0", day_of_month="1"),
    },
    "monitor-system-health": {
        "task": "core.tasks.monitor_system_health",
        "schedule": crontab(minute="*/30"),
    },
    "backup-database": {
        "task": "core.tasks.backup_database",
        "schedule": crontab(hour="7", minute="0"),
    },
    "optimize-images": {
        "task": "core.tasks.optimize_images",
        "schedule": crontab(hour="3", minute="30", day_of_week="sunday"),
    },
    "compress-old-logs": {
        "task": "core.tasks.compress_old_logs",
        "schedule": crontab(hour="1", minute="0", day_of_month="1"),
    },
    "clear-duplicate-history": {
        "task": "core.tasks.clear_duplicate_history_task",
        "schedule": crontab(hour="4", minute="0"),
        "kwargs": {
            "excluded_fields": [],
        },
    },
    "clear-old-history": {
        "task": "core.tasks.clear_old_history_task",
        "schedule": crontab(hour="5", minute="0"),
        "kwargs": {
            "days": 365,
        },
    },
    "clear-expired-notifications": {
        "task": "core.tasks.clear_expired_notifications_task",
        "schedule": crontab(hour="6", minute="0", day_of_month="*/2"),
    },
    "clear-carts-for-none-users": {
        "task": "core.tasks.clear_carts_for_none_users_task",
        "schedule": crontab(hour="4", minute="0", day_of_month="*/2"),
    },
    "clear-expired-sessions": {
        "task": "core.tasks.clear_expired_sessions_task",
        "schedule": crontab(hour="5", minute="0", day_of_week="sunday"),
    },
    "clear-all-cache": {
        "task": "core.tasks.clear_all_cache_task",
        "schedule": timedelta(days=30),
    },
    "clear-old-database-backups": {
        "task": "core.tasks.clear_old_database_backups",
        "schedule": crontab(hour="12", minute="0"),
    },
    "clear-blacklisted-tokens": {
        "task": "core.tasks.tasks.clear_blacklisted_tokens_task",
        "schedule": crontab(hour="2", minute="0"),
    },
    "clear-log-files": {
        "task": "core.tasks.clear_log_files_task",
        "schedule": crontab(hour="3", minute="0"),
    },
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [(REDIS_HOST, int(REDIS_PORT))]},
    },
}


APP_BASE_URL = getenv("APP_BASE_URL", "http://localhost:8000")
API_BASE_URL = getenv("API_BASE_URL", "http://localhost:8000")
AWS_STORAGE_BUCKET_NAME = getenv("AWS_STORAGE_BUCKET_NAME", "changeme")
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
    STATIC_BASE_URL,
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
    STATIC_BASE_URL,
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

CSRF_USE_SESSIONS = False
CSRF_COOKIE_NAME = "csrftoken"
CSRF_COOKIE_AGE = 60 * 60 * 24 * 7 * 52  # 1 year
CSRF_COOKIE_DOMAIN = getenv("CSRF_COOKIE_DOMAIN", "localhost")
CSRF_COOKIE_PATH = "/"
CSRF_COOKIE_SECURE = not DEBUG  # Only send CSRF cookie over HTTPS when DEBUG is False
CSRF_COOKIE_HTTPONLY = False  # Set to True to prevent JavaScript from reading the CSRF
CSRF_COOKIE_SAMESITE = (
    "Lax"  # 'Lax' or 'None'. Use 'None' only if necessary and ensure CSRF_COOKIE_SECURE is True
)
CSRF_HEADER_NAME = "HTTP_X_CSRFTOKEN"

# CSRF_TRUSTED_ORIGINS should include only the domains that are trusted to send POST requests to your application
CSRF_TRUSTED_ORIGINS = [
    APP_BASE_URL.replace("http://", "https://"),  # Force HTTPS
    API_BASE_URL.replace("http://", "https://"),  # Force HTTPS
    NUXT_BASE_URL.replace("http://", "https://"),  # Force HTTPS
    MEDIA_STREAM_BASE_URL.replace("http://", "https://"),  # Force HTTPS
    STATIC_BASE_URL.replace("http://", "https://"),  # Force HTTPS
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

# Currency
DEFAULT_CURRENCY = "EUR"
BASE_CURRENCY = "EUR"
CURRENCIES = ("USD", "EUR")
CURRENCY_CHOICES = [("USD", "USD $"), ("EUR", "EUR €")]

CONN_HEALTH_CHECKS = SYSTEM_ENV == "production"
ATOMIC_REQUESTS = SYSTEM_ENV == "production"
CONN_MAX_AGE = int(getenv("DJANGO_CONN_MAX_AGE", 30))
INDEX_MAXIMUM_EXPR_COUNT = 8000

DATABASES = {
    "default": {
        "ATOMIC_REQUESTS": SYSTEM_ENV == "production",
        "CONN_HEALTH_CHECKS": SYSTEM_ENV == "production",
        "TIME_ZONE": getenv("TIME_ZONE", "Europe/Athens"),
        "ENGINE": "django.db.backends.postgresql",
        "HOST": getenv("DB_HOST", "db"),
        "NAME": getenv("DB_NAME", "devdb"),
        "USER": getenv("DB_USER", "devuser"),
        "PASSWORD": getenv("DB_PASSWORD", "changeme"),
        "PORT": getenv("DB_PORT", "5432"),
        "OPTIONS": {
            "pool": {
                "min_size": 4,
                "max_size": 8,
                "timeout": 60,
            }
        },
    },
    "replica": {
        "TIME_ZONE": getenv("TIME_ZONE", "Europe/Athens"),
        "ENGINE": "django.db.backends.postgresql",
        "HOST": getenv("DB_HOST_TEST", "db_replica"),
        "NAME": getenv("DB_NAME_TEST", "devdb_replica"),
        "TEST": {
            "MIRROR": getenv("DB_TEST_MIRROR", "default"),
        },
    },
}

if SYSTEM_ENV == "ci":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "postgres",
            "USER": getenv("DB_USER", "devuser"),
            "PASSWORD": getenv("DB_PASSWORD", "changeme"),
            "HOST": "127.0.0.1",
            "PORT": "5432",
        }
    }

DBBACKUP_STORAGE = "django.core.files.storage.FileSystemStorage"
DBBACKUP_STORAGE_OPTIONS = {"location": path.join(BASE_DIR, "backups")}

# Maili settings
MEILISEARCH = {
    "HTTPS": getenv("MEILI_HTTPS", "False") == "True",
    "HOST": getenv("MEILI_HOST", "localhost"),
    "MASTER_KEY": getenv("MEILI_MASTER_KEY", "changeme"),
    "PORT": int(getenv("MEILI_PORT", 7700)),
    "TIMEOUT": int(getenv("MEILI_TIMEOUT", 30)),
    "CLIENT_AGENTS": None,
    "DEBUG": DEBUG,
    "SYNC": False,
    "OFFLINE": False,
}

SEED_DEFAULT_COUNT = int(getenv("SEED_DEFAULT_COUNT", 20))
SEED_BATCH_SIZE = int(getenv("SEED_BATCH_SIZE", 10))

EXTRA_SETTINGS_DEFAULTS = [
    {
        "name": "CHECKOUT_SHIPPING_PRICE",
        "type": "decimal",
        "value": 3.0,
    },
]

EMAIL_BACKEND = getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = getenv("EMAIL_HOST", "localhost")
EMAIL_PORT = getenv("EMAIL_PORT", "25")
EMAIL_HOST_USER = getenv("EMAIL_HOST_USER", "localhost@gmail.com")
EMAIL_HOST_PASSWORD = getenv("EMAIL_HOST_PASSWORD", "changeme")
EMAIL_USE_TLS = getenv("EMAIL_USE_TLS", "False") == "True"
DEFAULT_FROM_EMAIL = getenv("DEFAULT_FROM_EMAIL", "localhost@gmail.com")
ADMIN_EMAIL = getenv("ADMIN_EMAIL", "localhost@gmail.com")
INFO_EMAIL = getenv("INFO_EMAIL", "localhost@gmail.com")

REST_KNOX = {
    "TOKEN_TTL": timedelta(days=20),
    "AUTH_HEADER_PREFIX": "Bearer",
}
KNOX_TOKEN_MODEL = "knox.AuthToken"

MEASUREMENT_BIDIMENSIONAL_SEPARATOR = "/"

MFA_ADAPTER = "core.adapter.MFAAdapter"
MFA_RECOVERY_CODE_COUNT = 10
MFA_TOTP_PERIOD = 30
MFA_TOTP_DIGITS = 6
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes", "webauthn"]
MFA_PASSKEY_LOGIN_ENABLED = True
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = True if DEBUG else False
MFA_PASSKEY_SIGNUP_ENABLED = True

PARLER_DEFAULT_LANGUAGE_CODE = "el"
PARLER_LANGUAGES = {
    SITE_ID: (
        {
            "code": "el",
            "name": "greek",
        },
        {
            "code": "en",
            "name": "english",
        },
        {
            "code": "de",
            "name": "german",
        },
    ),
    "default": {
        "fallbacks": ["en"],
        "hide_untranslated": False,
    },
}
PARLER_ENABLE_CACHING = True

PHONENUMBER_DEFAULT_REGION = "GR"

ROSETTA_MESSAGES_PER_PAGE = 25
ROSETTA_ENABLE_TRANSLATION_SUGGESTIONS = True
ROSETTA_SHOW_AT_ADMIN_PANEL = True

# Security Settings
SECURE_SSL_REDIRECT = getenv("SECURE_SSL_REDIRECT", "False") == "True"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if not DEBUG else None
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG

SESSION_CACHE_ALIAS = "default"
SESSION_COOKIE_NAME = "sessionid"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 7 * 2
SESSION_COOKIE_DOMAIN = getenv("SESSION_COOKIE_DOMAIN", "localhost")
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_PATH = "/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_SAVE_EVERY_REQUEST = False
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_FILE_PATH = None
SESSION_SERIALIZER = "django.contrib.sessions.serializers.JSONSerializer"

SPECTACULAR_SETTINGS = {
    "TITLE": getenv("DJANGO_SPECTACULAR_SETTINGS_TITLE", "Django"),
    "DESCRIPTION": getenv("DJANGO_SPECTACULAR_SETTINGS_DESCRIPTION", "Django"),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAuthenticated"],
    "AUTHENTICATION_WHITELIST": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.BasicAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "SERVE_AUTHENTICATION": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.BasicAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.contrib.djangorestframework_camel_case.camelize_serializer_fields",
        "drf_spectacular.hooks.postprocess_schema_enums",
    ],
    "ENUM_NAME_OVERRIDES": {
        # Used by Checkout, Order, OrderCreateUpdate, PatchedOrderCreateUpdate
        "OrderStatusEnum": "order.enum.status_enum.OrderStatusEnum",
    },
}

STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]

STATICFILES_DIRS = [
    path.join(BASE_DIR, "static"),
]

if USE_AWS:
    # aws settings
    AWS_ACCESS_KEY_ID = getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = getenv("AWS_SECRET_ACCESS_KEY")
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = False
    AWS_S3_OBJECT_PARAMETERS = {"CacheControl": "max-age=86400"}
    # s3 static settings
    STATIC_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/"
    STATIC_ROOT = f"https://{AWS_S3_CUSTOM_DOMAIN}/static/"
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
    COMPRESS_STORAGE = "core.storages.StaticStorage"
    COMPRESS_OFFLINE_MANIFEST_STORAGE = "core.storages.StaticStorage"
    TINYMCE_JS_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/tinymce/tinymce.min.js"
    TINYMCE_JS_ROOT = f"https://{AWS_S3_CUSTOM_DOMAIN}/tinymce/"
elif not DEBUG:
    STATIC_URL = f"{STATIC_BASE_URL}/static/"
    STATIC_ROOT = path.join(BASE_DIR, "staticfiles")
    MEDIA_URL = f"{STATIC_BASE_URL}/media/"
    MEDIA_ROOT = path.join(BASE_DIR, "mediafiles")
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage",
        },
    }
else:
    STATIC_URL = "/static/"
    STATIC_ROOT = path.join(BASE_DIR, "staticfiles")
    MEDIA_URL = "/media/"
    MEDIA_ROOT = path.join(BASE_DIR, "mediafiles")
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }

# Django Compressor
COMPRESS_ENABLED = True
COMPRESS_ROOT = STATIC_ROOT
COMPRESS_URL = STATIC_URL

TINYMCE_DEFAULT_CONFIG = {
    "theme": "silver",
    "height": 500,
    "width": 960,
    "menubar": "file edit view insert format tools table help",
    "plugins": "advlist,autolink,lists,link,image,charmap,preview,anchor,"
    "searchreplace,visualblocks,code,fullscreen,insertdatetime,media,table,"
    "code,help,wordcount",
    "toolbar": "undo redo | bold italic underline strikethrough | fontselect fontsizeselect formatselect | alignleft "
    "aligncenter alignright alignjustify | outdent indent |  numlist bullist checklist | forecolor "
    "backcolor casechange permanentpen formatpainter removeformat | pagebreak | charmap emoticons | "
    "fullscreen  preview save print | insertfile image media pageembed template link anchor codesample | "
    "a11ycheck ltr rtl | showcomments addcomment code",
    "images_upload_url": "/upload_image",
    "relative_urls": False,
    "remove_script_host": False,
    "entity_encoding": "raw",
}

TINYMCE_COMPRESSOR = False

DATA_UPLOAD_MAX_MEMORY_SIZE = 5242880

# Related Posts Strategies Configuration
RELATED_POSTS_STRATEGIES = [
    {
        "strategy": "blog.strategies.default_related_posts_strategy.DefaultRelatedPostsStrategy",
        "weight": 0.6,
    },
    {
        "strategy": "blog.strategies.tag_based_related_posts_strategy.TagBasedRelatedPostsStrategy",
        "weight": 0.4,
    },
]
RELATED_POSTS_LIMIT = 8

# Logging
timestamp = datetime.now().strftime("%d-%m-%Y")
log_dir = path.join(BASE_DIR, "logs")
makedirs(log_dir, exist_ok=True)

django_log_file_path = path.join(log_dir, f"django_logs_{timestamp}.log")

logging_level = getenv("LOGGING_LEVEL", "INFO")
backup_count = int(getenv("LOG_BACKUP_COUNT", 30))

LOGGING = {
    "version": 1,
    "formatters": {
        "standard": {
            "format": "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] %(message)s",
            "datefmt": "%d/%b/%Y %H:%M:%S",
        },
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "[%(asctime)s] %(levelname)s | %(funcName)s | %(name)s | %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "json": {
            "format": '{"timestamp": "%(asctime)s", "level": "%(levelname)s",'
            ' "module": "%(module)s", "message": "%(message)s"}',
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        },
    },
    "filters": {
        "require_debug_true": {
            "()": "django.utils.log.RequireDebugTrue",
        },
        "require_debug_false": {
            "()": "django.utils.log.RequireDebugFalse",
        },
    },
    "handlers": {
        "file": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "filename": django_log_file_path,
            "when": "midnight",
            "interval": 1,
            "backupCount": backup_count,
            "level": logging_level,
            "formatter": "verbose",
        },
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "level": logging_level,
            "propagate": True,
        },
    },
}
