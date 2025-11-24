import datetime
from os import getenv, makedirs, path
from pathlib import Path

import django_stubs_ext
import dotenv
from celery.schedules import crontab
from corsheaders.defaults import (
    default_headers,
)
from django.templatetags.static import static
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

django_stubs_ext.monkeypatch()

BASE_DIR = Path(__file__).resolve().parent

dotenv_file = BASE_DIR / ".env"


def load_dotenv_file():
    if path.isfile(dotenv_file):
        dotenv.load_dotenv(dotenv_file)


load_dotenv_file()

SYSTEM_ENV = getenv("SYSTEM_ENV", "dev")

SECRET_KEY = getenv("SECRET_KEY", "changeme")

DEBUG = getenv("DEBUG", "True") == "True"

DJANGO_ADMIN_FORCE_ALLAUTH = (
    getenv("DJANGO_ADMIN_FORCE_ALLAUTH", "True") == "True"
)

INTERNAL_IPS = [
    "127.0.0.1",
    "0.0.0.0",
]


SERIALIZATION_MODULES = {"json": "djmoney.serializers"}

if DEBUG:
    import socket

    hostname, aliaslist, ips = socket.gethostbyname_ex(socket.gethostname())
    INTERNAL_IPS = [ip[: ip.rfind(".")] + ".1" for ip in ips] + [
        "127.0.0.1",
        "10.0.2.2",
    ]

APP_MAIN_HOST_NAME = getenv("APP_MAIN_HOST_NAME", "localhost")
NUXT_BASE_URL = getenv("NUXT_BASE_URL", "http://localhost:3000")
NUXT_BASE_DOMAIN = getenv("NUXT_BASE_DOMAIN", "localhost:3000")
MEDIA_STREAM_BASE_URL = getenv("MEDIA_STREAM_BASE_URL", "http://localhost:3003")
STATIC_BASE_URL = getenv("STATIC_BASE_URL", "http://localhost:8000")

ALLOWED_HOSTS: list[str] = []

additional_hosts = getenv("ALLOWED_HOSTS", "*").split(",")
ALLOWED_HOSTS.extend(filter(None, additional_hosts))

if "testserver" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append("testserver")

USE_X_FORWARDED_HOST = getenv("USE_X_FORWARDED_HOST", "False") == "True"

DJANGO_APPS = [
    "daphne",
    "unfold.apps.BasicAppConfig",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.simple_history",
    "admin.apps.MyAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.postgres",
]

LOCAL_APPS = [
    "core",
    "user",
    "product",
    "order",
    "search",
    "blog",
    "vat",
    "country",
    "region",
    "pay_way",
    "cart",
    "notification",
    "contact",
    "tag",
    "meili",
]

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
    "extra_settings",
    "knox",
    "simple_history",
    "djstripe",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
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

LOGIN_URL = "/admin/"
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

WSGI_APPLICATION = "wsgi.application"
ASGI_APPLICATION = "asgi.application"

AUTH_USER_MODEL = "user.UserAccount"

LANGUAGE_CODE = getenv("LANGUAGE_CODE", "el")
TIME_ZONE = getenv("TIME_ZONE", "Europe/Athens")
USE_I18N = getenv("USE_I18N", "True") == "True"
USE_TZ = getenv("USE_TZ", "True") == "True"

SITE_ID = int(getenv("SITE_ID", "1"))

LANGUAGES = (
    ("el", _("Greek")),
    ("en", _("English")),
    ("de", _("German")),
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

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
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "knox.auth.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": None if DEBUG else "100000/day",
        "user": None if DEBUG else "150000/day",
        "burst": None if DEBUG else "5/minute",
    },
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "core.filters.camel_case_ordering.CamelCaseOrderingFilter",
        "rest_framework.filters.SearchFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "core.pagination.page_number.PageNumberPaginator",
    "PAGE_SIZE": 12,
    "DEFAULT_RENDERER_CLASSES": (
        "djangorestframework_camel_case.render.CamelCaseJSONRenderer",
        "djangorestframework_camel_case.render.CamelCaseBrowsableAPIRenderer",
    ),
    "DEFAULT_PARSER_CLASSES": (
        "djangorestframework_camel_case.parser.CamelCaseFormParser",
        "djangorestframework_camel_case.parser.CamelCaseMultiPartParser",
        "djangorestframework_camel_case.parser.CamelCaseJSONParser",
    ),
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
        "SCOPE": ["read:user", "user:email", "repo"],
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

ACCOUNT_CHANGE_EMAIL = DEBUG
ACCOUNT_USER_MODEL_USERNAME_FIELD = "username"
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_NOTIFICATIONS = True
ACCOUNT_USERNAME_MIN_LENGTH = 2
ACCOUNT_USERNAME_MAX_LENGTH = 30
ACCOUNT_LOGIN_METHODS = {"email"}
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

DEFAULT_CACHE_KEY_PREFIX = getenv("DEFAULT_CACHE_KEY_PREFIX", "default")
DEFAULT_CACHE_VERSION = int(getenv("DEFAULT_CACHE_VERSION", "1"))
DEFAULT_CACHE_TTL = int(getenv("DEFAULT_CACHE_TTL", "7200"))
DISABLE_CACHE = getenv("DISABLE_CACHE", "False").lower() == "true"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": DEFAULT_CACHE_KEY_PREFIX,
        "VERSION": DEFAULT_CACHE_VERSION,
        "TIMEOUT": DEFAULT_CACHE_TTL,
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


# Broker & Results
CELERY_BROKER_URL = getenv(
    "CELERY_BROKER_URL", "amqp://guest:guest@rabbitmq:5672//"
)
CELERY_RESULT_BACKEND = getenv("CELERY_RESULT_BACKEND", "django-db")
CELERY_CACHE_BACKEND = getenv("CELERY_CACHE_BACKEND", "django-cache")

# Task execution
CELERY_TASK_TRACK_STARTED = (
    False  # Disable to reduce DB load and avoid connection issues
)
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_SOFT_TIME_LIMIT = 1500
CELERY_TASK_TIME_LIMIT = 1800
CELERY_TASK_STORE_ERRORS_EVEN_IF_IGNORED = True

# Default retry policy for all tasks
CELERY_TASK_AUTORETRY_FOR = (
    Exception,  # Retry on any exception
)
CELERY_TASK_RETRY_BACKOFF = True  # Exponential backoff
CELERY_TASK_RETRY_BACKOFF_MAX = 600  # Max 10 minutes between retries
CELERY_TASK_RETRY_JITTER = True  # Add randomness to prevent thundering herd
CELERY_TASK_MAX_RETRIES = 5  # Retry up to 5 times

# Timezone (use UTC)
CELERY_ENABLE_UTC = False
CELERY_TIMEZONE = getenv("TIME_ZONE", "Europe/Athens")

# Connection settings
CELERY_BROKER_HEARTBEAT = 30
CELERY_BROKER_POOL_LIMIT = 50
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "confirm_publish": True,
    "max_retries": 3,
    "interval_start": 0,
    "interval_step": 0.2,
    "interval_max": 0.5,
}
CELERY_BROKER_CONNECTION_TIMEOUT = 30
CELERY_BROKER_CONNECTION_RETRY = True
CELERY_BROKER_CONNECTION_MAX_RETRIES = 100

# Worker settings
CELERY_WORKER_SEND_TASK_EVENTS = True
CELERY_TASK_SEND_SENT_EVENT = True
CELERY_WORKER_MAX_TASKS_PER_CHILD = (
    50  # Restart worker after 50 tasks to prevent memory leaks
)
CELERY_WORKER_MAX_MEMORY_PER_CHILD = 250000  # 250MB limit per worker
CELERY_WORKER_DISABLE_RATE_LIMITS = False
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # Fetch one task at a time

CELERY_RESULT_BACKEND_ALWAYS_RETRY = True
CELERY_RESULT_BACKEND_MAX_RETRIES = 3
CELERY_RESULT_EXTENDED = True
CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS = {"retry_policy": {"timeout": 5.0}}
CELERY_TASK_RESULT_EXPIRES = 3600  # Results expire after 1 hour
CELERY_RESULT_PERSISTENT = False  # Don't persist to disk for better performance

# Serialization
CELERY_ACCEPT_CONTENT = ["application/json"]
CELERY_RESULT_SERIALIZER = "json"
CELERY_TASK_SERIALIZER = "json"

# Beat scheduler
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_BEAT_MAX_LOOP_INTERVAL = 300

# Development safety
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_EAGER_PROPAGATES = False

SCHEDULE_PRESETS = {
    # Daily schedules
    "daily_2am": crontab(hour="2", minute="0"),
    "daily_3am": crontab(hour="3", minute="0"),
    "daily_4am": crontab(hour="4", minute="0"),
    "daily_5am": crontab(hour="5", minute="0"),
    "daily_6am": crontab(hour="6", minute="0"),
    "daily_7am": crontab(hour="7", minute="0"),
    "daily_noon": crontab(hour="12", minute="0"),
    # Weekly schedules
    "weekly_sunday_5am": crontab(hour="5", minute="0", day_of_week="0"),
    "weekly_sunday_3am": crontab(hour="3", minute="0", day_of_week="0"),
    "weekly_monday_4am": crontab(hour="4", minute="0", day_of_week="1"),
    # Monthly schedules
    "monthly_first_6am": crontab(hour="6", minute="0", day_of_month="1"),
    "monthly_first_4am": crontab(hour="4", minute="0", day_of_month="1"),
    # Bi-monthly schedules
    "bimonthly_4am": crontab(hour="4", minute="0", day_of_month="1,15"),
    "bimonthly_6am": crontab(hour="6", minute="0", day_of_month="1,15"),
    # Frequent schedules
    "every_minute": crontab(minute="*"),
    "every_5_minute": crontab(minute="*/5"),
    "every_30_min": crontab(minute="*/30"),
    "every_hour": crontab(minute="0"),
}


def get_celery_beat_schedule():
    base_schedule = {
        "monitor-system-health": {
            "task": "core.tasks.monitor_system_health",
            "schedule": SCHEDULE_PRESETS["daily_5am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_minute"],
        },
        "scheduled-database-backup": {
            "task": "core.tasks.scheduled_database_backup",
            "schedule": SCHEDULE_PRESETS["daily_3am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_minute"],
        },
        "cleanup-old-backups": {
            "task": "core.tasks.cleanup_old_backups",
            "schedule": SCHEDULE_PRESETS["weekly_sunday_5am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_minute"],
            "kwargs": {"days": 30, "backup_dir": "backups"},
        },
        "clear-duplicate-history": {
            "task": "core.tasks.clear_duplicate_history_task",
            "schedule": SCHEDULE_PRESETS["daily_5am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_minute"],
            "kwargs": {"excluded_fields": [], "minutes": None},
        },
        "clear-old-history": {
            "task": "core.tasks.clear_old_history_task",
            "schedule": SCHEDULE_PRESETS["weekly_sunday_3am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_minute"],
            "kwargs": {"days": 365},
        },
        "clear-expired-sessions": {
            "task": "core.tasks.clear_expired_sessions_task",
            "schedule": SCHEDULE_PRESETS["weekly_monday_4am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_minute"],
        },
        "send-inactive-user-notifications": {
            "task": "core.tasks.send_inactive_user_notifications",
            "schedule": SCHEDULE_PRESETS["monthly_first_6am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_minute"],
        },
        "clear-all-cache": {
            "task": "core.tasks.clear_all_cache_task",
            "schedule": SCHEDULE_PRESETS["monthly_first_4am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_minute"],
        },
        "cleanup-abandoned-carts": {
            "task": "core.tasks.cleanup_abandoned_carts",
            "schedule": SCHEDULE_PRESETS["daily_4am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_minute"],
        },
        "cleanup-old-guest-carts": {
            "task": "core.tasks.cleanup_old_guest_carts",
            "schedule": SCHEDULE_PRESETS["daily_6am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_minute"],
        },
        "clear-expired-notifications": {
            "task": "core.tasks.clear_expired_notifications_task",
            "schedule": SCHEDULE_PRESETS["bimonthly_6am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_minute"],
            "kwargs": {"days": 365},
        },
        "sync-meilisearch-indexes": {
            "task": "core.tasks.sync_meilisearch_indexes",
            "schedule": SCHEDULE_PRESETS["daily_2am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_minute"],
        },
    }

    if path.exists("/.dockerenv") and not getenv("KUBERNETES_SERVICE_HOST"):
        base_schedule.update(
            {
                "clear-development-logs": {
                    "task": "core.tasks.clear_development_log_files_task",
                    "schedule": SCHEDULE_PRESETS["daily_4am"]
                    if not DEBUG
                    else SCHEDULE_PRESETS["every_minute"],
                    "kwargs": {"days": 7},
                },
            }
        )

    return base_schedule


CELERY_BEAT_SCHEDULE = get_celery_beat_schedule()

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
CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOW_CREDENTIALS = True
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
    "location",
)

CSRF_USE_SESSIONS = False
CSRF_COOKIE_NAME = "csrftoken"
CSRF_COOKIE_AGE = 60 * 60 * 24 * 7 * 52  # 1 year
CSRF_COOKIE_DOMAIN = getenv("CSRF_COOKIE_DOMAIN", "localhost")
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

# Security Settings
SECURE_SSL_REDIRECT = getenv("SECURE_SSL_REDIRECT", "False") == "True"
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https") if not DEBUG else None
)
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG

# Currency
DEFAULT_CURRENCY = getenv("DEFAULT_CURRENCY", "EUR")
CURRENCIES = ("EUR", "USD")
CURRENCY_CHOICES = [("EUR", "EUR €"), ("USD", "USD $")]

CONN_HEALTH_CHECKS = False
ATOMIC_REQUESTS = False
CONN_MAX_AGE = 600
INDEX_MAXIMUM_EXPR_COUNT = 8000

DATABASES = {
    "default": {
        "ATOMIC_REQUESTS": False,
        "AUTOCOMMIT": True,
        "CONN_HEALTH_CHECKS": True,
        "CONN_MAX_AGE": 0,
        "TIME_ZONE": getenv("TIME_ZONE", "Europe/Athens"),
        "ENGINE": "django.db.backends.postgresql",
        "HOST": getenv("DB_HOST", "db"),
        "NAME": getenv("DB_NAME", "postgres"),
        "USER": getenv("DB_USER", "postgres"),
        "PASSWORD": getenv("DB_PASSWORD", "postgres"),
        "PORT": getenv("DB_PORT", "5432"),
        "OPTIONS": {
            "connect_timeout": 5,
            "options": "-c statement_timeout=30000 -c idle_in_transaction_session_timeout=10000",
        },
    },
}

if SYSTEM_ENV == "ci":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "postgres",
            "USER": getenv("DB_USER", "postgres"),
            "PASSWORD": getenv("DB_PASSWORD", "postgres"),
            "HOST": "127.0.0.1",
            "PORT": "5432",
        }
    }

# Maili settings
MEILISEARCH = {
    "HTTPS": getenv("MEILI_HTTPS", "False") == "True",
    "HOST": getenv("MEILI_HOST", "localhost"),
    "MASTER_KEY": getenv("MEILI_MASTER_KEY", "changeme"),
    "PORT": int(getenv("MEILI_PORT", "7700")),
    "TIMEOUT": int(getenv("MEILI_TIMEOUT", "30")),
    "CLIENT_AGENTS": None,
    "DEBUG": DEBUG,
    "SYNC": DEBUG,
    "OFFLINE": bool(getenv("MEILI_OFFLINE", "False")),
}

SEED_DEFAULT_COUNT = int(getenv("SEED_DEFAULT_COUNT", "10"))
SEED_BATCH_SIZE = int(getenv("SEED_BATCH_SIZE", "10"))

EXTRA_SETTINGS_DEFAULTS = [
    {
        "name": "CHECKOUT_SHIPPING_PRICE",
        "type": "decimal",
        "value": 3.00,
    },
    {
        "name": "FREE_SHIPPING_THRESHOLD",
        "type": "decimal",
        "value": 50.00,
    },
    {
        "name": "CART_ABANDONED_HOURS",
        "type": "int",
        "value": 24,
    },
    {
        "name": "DEFAULT_WEIGHT_UNIT",
        "type": "string",
        "value": "kg",
    },
    {
        "name": "SUBSCRIPTION_CONFIRMATION_URL",
        "type": "string",
        "value": "https://example.com/confirm/{token}/",
    },
]

EMAIL_BACKEND = getenv(
    "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = getenv("EMAIL_HOST", "localhost")
EMAIL_PORT = getenv("EMAIL_PORT", "25")
EMAIL_HOST_USER = getenv("EMAIL_HOST_USER", "localhost@gmail.com")
EMAIL_HOST_PASSWORD = getenv("EMAIL_HOST_PASSWORD", "changeme")
EMAIL_USE_TLS = getenv("EMAIL_USE_TLS", "False") == "True"
DEFAULT_FROM_EMAIL = getenv("DEFAULT_FROM_EMAIL", "localhost@gmail.com")
ADMIN_EMAIL = getenv("ADMIN_EMAIL", "localhost@gmail.com")
INFO_EMAIL = getenv("INFO_EMAIL", "localhost@gmail.com")

REST_KNOX = {
    "TOKEN_TTL": datetime.timedelta(days=20),
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
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = DEBUG
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

LANGUAGE_COUNTRY_MAPPING = {
    "el": {
        "alpha_2": "GR",
        "alpha_3": "GRC",
        "iso_cc": 300,
        "phone_code": "30",
        "names": {"el": "Ελλάδα", "en": "Greece", "de": "Griechenland"},
    },
    "en": {
        "alpha_2": "GB",
        "alpha_3": "GBR",
        "iso_cc": 826,
        "phone_code": "44",
        "names": {
            "el": "Ηνωμένο Βασίλειο",
            "en": "United Kingdom",
            "de": "Vereinigtes Königreich",
        },
    },
    "de": {
        "alpha_2": "DE",
        "alpha_3": "DEU",
        "iso_cc": 276,
        "phone_code": "49",
        "names": {"el": "Γερμανία", "en": "Germany", "de": "Deutschland"},
    },
}

COUNTRY_REGIONS_MAPPING = {
    "GR": [
        {
            "alpha": "EMT",
            "names": {
                "el": "Ανατολική Μακεδονία και Θράκη",
                "en": "East Macedonia and Thrace",
                "de": "Ostmakedonien und Thrakien",
            },
        },
        {
            "alpha": "CMA",
            "names": {
                "el": "Κεντρική Μακεδονία",
                "en": "Central Macedonia",
                "de": "Zentralmakedonien",
            },
        },
        {
            "alpha": "WMA",
            "names": {
                "el": "Δυτική Μακεδονία",
                "en": "West Macedonia",
                "de": "Westmakedonien",
            },
        },
        {
            "alpha": "EPI",
            "names": {"el": "Ήπειρος", "en": "Epirus", "de": "Epirus"},
        },
        {
            "alpha": "THS",
            "names": {"el": "Θεσσαλία", "en": "Thessaly", "de": "Thessalien"},
        },
        {
            "alpha": "ION",
            "names": {
                "el": "Ιόνια Νησιά",
                "en": "Ionian Islands",
                "de": "Ionische Inseln",
            },
        },
        {
            "alpha": "WGR",
            "names": {
                "el": "Δυτική Ελλάδα",
                "en": "West Greece",
                "de": "Westgriechenland",
            },
        },
        {
            "alpha": "CGR",
            "names": {
                "el": "Στερεά Ελλάδα",
                "en": "Central Greece",
                "de": "Mittelgriechenland",
            },
        },
        {
            "alpha": "ATT",
            "names": {"el": "Αττική", "en": "Attica", "de": "Attika"},
        },
        {
            "alpha": "PEL",
            "names": {
                "el": "Πελοπόννησος",
                "en": "Peloponnese",
                "de": "Peloponnes",
            },
        },
        {
            "alpha": "NAE",
            "names": {
                "el": "Βόρειο Αιγαίο",
                "en": "North Aegean",
                "de": "Nordägäis",
            },
        },
        {
            "alpha": "SAE",
            "names": {
                "el": "Νότιο Αιγαίο",
                "en": "South Aegean",
                "de": "Südägάις",
            },
        },
        {
            "alpha": "CRT",
            "names": {"el": "Κρήτη", "en": "Crete", "de": "Kreta"},
        },
    ],
    "GB": [
        {
            "alpha": "ENG",
            "names": {"el": "Αγγλία", "en": "England", "de": "England"},
        },
        {
            "alpha": "SCT",
            "names": {"el": "Σκωτία", "en": "Scotland", "de": "Schottland"},
        },
        {
            "alpha": "WLS",
            "names": {"el": "Ουαλία", "en": "Wales", "de": "Wales"},
        },
        {
            "alpha": "NIR",
            "names": {
                "el": "Βόρεια Ιρλανδία",
                "en": "Northern Ireland",
                "de": "Nordirland",
            },
        },
    ],
    "DE": [
        {
            "alpha": "BW",
            "names": {
                "el": "Βάδη-Βυρτεμβέργη",
                "en": "Baden-Württemberg",
                "de": "Baden-Württemberg",
            },
        },
        {
            "alpha": "BY",
            "names": {"el": "Βαυαρία", "en": "Bavaria", "de": "Bayern"},
        },
        {
            "alpha": "BE",
            "names": {"el": "Βερολίνο", "en": "Berlin", "de": "Berlin"},
        },
        {
            "alpha": "BB",
            "names": {
                "el": "Βρανδεμβούργο",
                "en": "Brandenburg",
                "de": "Brandenburg",
            },
        },
        {
            "alpha": "HB",
            "names": {"el": "Βρέμη", "en": "Bremen", "de": "Bremen"},
        },
        {
            "alpha": "HH",
            "names": {"el": "Αμβούργο", "en": "Hamburg", "de": "Hamburg"},
        },
        {"alpha": "HE", "names": {"el": "Έσση", "en": "Hesse", "de": "Hessen"}},
        {
            "alpha": "MV",
            "names": {
                "el": "Μεκλεμβούργο-Πομερανία",
                "en": "Mecklenburg-Western Pomerania",
                "de": "Mecklenburg-Vorpommern",
            },
        },
        {
            "alpha": "NI",
            "names": {
                "el": "Κάτω Σαξονία",
                "en": "Lower Saxony",
                "de": "Niedersachsen",
            },
        },
        {
            "alpha": "NW",
            "names": {
                "el": "Βόρεια Ρηνανία-Βεστφαλία",
                "en": "North Rhine-Westphalia",
                "de": "Nordrhein-Westfalen",
            },
        },
        {
            "alpha": "RP",
            "names": {
                "el": "Ρηνανία-Παλατινάτο",
                "en": "Rhineland-Palatinate",
                "de": "Rheinland-Pfalz",
            },
        },
        {
            "alpha": "SL",
            "names": {"el": "Σάαρλαντ", "en": "Saarland", "de": "Saarland"},
        },
        {
            "alpha": "SN",
            "names": {"el": "Σαξονία", "en": "Saxony", "de": "Sachsen"},
        },
        {
            "alpha": "ST",
            "names": {
                "el": "Σαξονία-Άνχαλτ",
                "en": "Saxony-Anhalt",
                "de": "Sachsen-Anhalt",
            },
        },
        {
            "alpha": "SH",
            "names": {
                "el": "Σλέσβιχ-Χολστάιν",
                "en": "Schleswig-Holstein",
                "de": "Schleswig-Holstein",
            },
        },
        {
            "alpha": "TH",
            "names": {"el": "Θουριγγία", "en": "Thuringia", "de": "Thüringen"},
        },
    ],
}

PHONENUMBER_DEFAULT_REGION = "GR"

ROSETTA_MESSAGES_PER_PAGE = 25
ROSETTA_ENABLE_TRANSLATION_SUGGESTIONS = True
ROSETTA_SHOW_AT_ADMIN_PANEL = True

UNFOLD = {
    "SITE_TITLE": getenv("UNFOLD_SITE_TITLE", "GrooveShop Title"),
    "SITE_HEADER": getenv("UNFOLD_SITE_HEADER", "GrooveShop Header"),
    "SITE_SUBHEADER": getenv("UNFOLD_SITE_SUBHEADER", "GrooveShop SubHeader"),
    "SITE_SYMBOL": "speed",
    "SITE_ICON": {
        "light": lambda request: static("icon-light.svg"),
        "dark": lambda request: static("icon-dark.svg"),
    },
    "SITE_LOGO": {
        "light": lambda request: static("logo-light.svg"),
        "dark": lambda request: static("logo-dark.svg"),
    },
    "SITE_FAVICONS": [
        {
            "rel": "icon",
            "sizes": "32x32",
            "type": "image/svg+xml",
            "href": lambda request: static("favicon/favicon.svg"),
        },
    ],
    "COLORS": {
        "primary": {
            "50": "225 231 255",
            "100": "198 211 255",
            "200": "158 183 255",
            "300": "117 153 255",
            "400": "68  118 255",
            "500": "0 61 255",
            "600": "0 39 165",
            "700": "0 31 131",
            "800": "0 24 102",
            "900": "0 16 67",
            "950": "0 10 43",
        },
    },
    "SHOW_LANGUAGES": True,
    "SITE_DROPDOWN": [
        {
            "icon": "cached",
            "title": _("Cache"),
            "link": reverse_lazy("admin:clear-cache"),
        },
        {
            "icon": "email",
            "title": _("Email Templates"),
            "link": reverse_lazy("email_templates:management"),
        },
    ],
}

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
    "COMPONENT_SPLIT_REQUEST": True,
    "COMPONENT_NO_READ_ONLY_REQUIRED": False,
    "ENFORCE_NON_BLANK_FIELDS": False,
    "ENUM_ADD_EXPLICIT_BLANK_NULL_CHOICE": True,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAuthenticated"],
    "AUTHENTICATION_WHITELIST": [
        "knox.auth.TokenAuthentication",
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.BasicAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "SERVE_AUTHENTICATION": [
        "knox.auth.TokenAuthentication",
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.BasicAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.contrib.djangorestframework_camel_case.camelize_serializer_fields",
        "drf_spectacular.hooks.postprocess_schema_enums",
        "core.api.schema.postprocess_schema_parameters_to_accept_strings",
    ],
    "PREPROCESSING_HOOKS": [
        "drf_spectacular.hooks.preprocess_exclude_path_format",
    ],
    "ENUM_NAME_OVERRIDES": {
        "OrderStatus": "order.enum.status.OrderStatus",
        "ReviewStatus": "product.enum.review.ReviewStatus",
        "SubscriptionStatus": "user.models.subscription.UserSubscription.SubscriptionStatus",
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
    STATIC_ROOT = path.join(BASE_DIR, "web", "staticfiles")
    MEDIA_URL = f"{STATIC_BASE_URL}/media/"
    MEDIA_ROOT = path.join(BASE_DIR, "web", "mediafiles")
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

FILE_UPLOAD_MAX_MEMORY_SIZE = 2621440

BLOG_COMMENT_AUTO_APPROVE = bool(getenv("BLOG_COMMENT_AUTO_APPROVE", "True"))

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
IS_KUBERNETES = getenv("KUBERNETES_SERVICE_HOST") is not None
IS_DOCKER = path.exists("/.dockerenv")
IS_DEVELOPMENT = DEBUG or SYSTEM_ENV == "dev"

logging_level = getenv("LOGGING_LEVEL", "INFO")

if IS_KUBERNETES:
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "format": '{"timestamp": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "module": "%(module)s", "function": "%(funcName)s", "line": %(lineno)d, "process": "%(process)d", "thread": "%(thread)d", "pod": "%(hostname)s", "message": "%(message)s"}',
                "datefmt": "%Y-%m-%dT%H:%M:%S",
            },
            "console": {
                "format": "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] %(funcName)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "filters": {
            "add_hostname": {
                "()": "core.logging.HostnameFilter",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "json"
                if SYSTEM_ENV == "production"
                else "console",
                "level": logging_level,
                "filters": ["add_hostname"],
            },
        },
        "root": {
            "handlers": ["console"],
            "level": logging_level,
        },
        "loggers": {
            "django": {
                "level": "WARNING",
                "propagate": True,
            },
            "celery": {
                "level": logging_level,
                "propagate": True,
            },
        },
    }

elif IS_DOCKER or IS_DEVELOPMENT:
    log_dir = path.join(BASE_DIR, "logs")
    makedirs(log_dir, exist_ok=True)

    container_id = getenv("HOSTNAME", "dev")[:12]
    app_log_file = path.join(log_dir, f"grooveshop_{container_id}.log")

    backup_count = int(getenv("LOG_BACKUP_COUNT", "30"))

    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "verbose": {
                "format": "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] %(funcName)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "simple": {
                "format": "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
                "datefmt": "%H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "simple",
                "level": "INFO",
            },
            "app_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": app_log_file,
                "maxBytes": 10 * 1024 * 1024,
                "backupCount": backup_count,
                "level": logging_level,
                "formatter": "verbose",
            },
        },
        "root": {
            "handlers": ["console", "app_file"],
            "level": logging_level,
        },
        "loggers": {
            "django": {
                "level": "WARNING",
                "propagate": True,
            },
            "celery": {
                "level": logging_level,
                "propagate": True,
            },
        },
    }

else:
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "simple": {
                "format": "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "simple",
            },
        },
        "root": {
            "handlers": ["console"],
            "level": "INFO",
        },
    }

# PAYMENT SETTINGS
# Stripe Configuration (dj-stripe format)
STRIPE_LIVE_SECRET_KEY = getenv("STRIPE_LIVE_SECRET_KEY", "sk_live_...")
STRIPE_TEST_SECRET_KEY = getenv("STRIPE_TEST_SECRET_KEY", "sk_test_...")
STRIPE_LIVE_MODE = not DEBUG
DJSTRIPE_FOREIGN_KEY_TO_FIELD = "id"
DJSTRIPE_WEBHOOK_VALIDATION = "verify_signature"
DJSTRIPE_WEBHOOK_SECRET = getenv("DJSTRIPE_WEBHOOK_SECRET", "whsec_...")


# SHIPPING SETTINGS
FEDEX_API_KEY = "fedex_api_key_example"
FEDEX_ACCOUNT_NUMBER = "fedex_account_number_example"
UPS_API_KEY = "ups_api_key_example"
UPS_ACCOUNT_NUMBER = "ups_account_number_example"
