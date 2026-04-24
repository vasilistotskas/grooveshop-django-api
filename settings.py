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

SECRET_KEY = getenv("SECRET_KEY", "")
if not SECRET_KEY:
    if SYSTEM_ENV == "production":
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(
            "SECRET_KEY environment variable is required in production."
        )
    SECRET_KEY = "insecure-dev-key-do-not-use-in-production"

DEBUG = getenv("DEBUG", "False") == "True"

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
SITE_NAME = getenv("SITE_NAME", "Grooveshop")

# django-tenants rejects unregistered domains before Django's host
# validation, so ALLOWED_HOSTS=["*"] is safe. Dynamic tenant domains
# can't be statically listed.
ALLOWED_HOSTS: list[str] = ["*"]

USE_X_FORWARDED_HOST = getenv("USE_X_FORWARDED_HOST", "True") == "True"

# ─── Multi-tenancy: SHARED vs TENANT apps ──────────────────
# SHARED_APPS live in the public schema (platform infrastructure + global data).
# TENANT_APPS live in each tenant schema (store-specific data).
# Apps in BOTH must appear in both lists (e.g. contenttypes, auth).

SHARED_APPS = [
    "django_tenants",
    "tenant",
    # Django core (platform admin in public schema)
    "daphne",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.postgres",
    "django.contrib.humanize",
    # Admin UI
    "unfold.apps.BasicAppConfig",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.simple_history",
    "admin.apps.MyAdminConfig",
    # Infrastructure (framework-level, no per-tenant data)
    "corsheaders",
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    "rosetta",
    "storages",
    "core",
    "devtools",
    # Global reference data (identical across ALL tenants)
    "country",
    "region",
    # User model must be in SHARED for AUTH_USER_MODEL FK resolution
    # in public-schema auth migrations. Tenant schemas have their own copy.
    "user",
]

TENANT_APPS = [
    # Required in BOTH by django-tenants
    "django.contrib.contenttypes",
    "django.contrib.auth",
    # User & Auth (isolated per-tenant)
    "user",
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
    "knox",
    # Domain apps (store-specific data)
    "product",
    "order",
    "cart",
    "blog",
    "search",
    "notification",
    "contact",
    "loyalty",
    "page_config",
    # Per-tenant reference data
    "vat",
    "pay_way",
    "tag",
    # Third-party per-tenant
    "mptt",
    "parler",
    "django_filters",
    "djmoney",
    "phonenumber_field",
    "tinymce",
    "django_celery_beat",
    "django_celery_results",
    "extra_settings",
    "simple_history",
    "djstripe",
    "meili",
]

INSTALLED_APPS = list(SHARED_APPS) + [
    app for app in TENANT_APPS if app not in SHARED_APPS
]

MIDDLEWARE = [
    "django_tenants.middleware.main.TenantMainMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "core.middleware.correlation_id.CorrelationIdMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "core.middleware.translation_reload.TranslationReloadMiddleware",
    "django.middleware.common.CommonMiddleware",
    "tenant.middleware.TenantCsrfMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.allauth_ratelimit.AllAuthRateLimitMiddleware",
    "core.middleware.idempotency.IdempotencyMiddleware",  # Idempotency-Key header replay protection
    "search.middleware.SearchAnalyticsMiddleware",  # Search analytics tracking
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "allauth.usersessions.middleware.UserSessionsMiddleware",
    "djangorestframework_camel_case.middleware.CamelCaseMiddleWare",
    "simple_history.middleware.HistoryRequestMiddleware",
    "core.middleware.asgi_compat.ASGICompatMiddleware",  # ASGI compatibility for Rosetta
    "core.middleware.stripe_webhook.StripeWebhookDebugMiddleware",  # Stripe webhook debugging
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            path.join(BASE_DIR, "core/templates"),
            path.join(BASE_DIR, "templates"),
        ],
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
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": None if DEBUG else "100000/day",
        "user": None if DEBUG else "150000/day",
        "burst": None if DEBUG else "5/minute",
        # Per-endpoint scopes. Applied via throttle_classes on specific views
        # (ContactCreateThrottle, PaymentAttemptThrottle, PaymentAttemptAnonThrottle
        # in core.api.throttling) — the global anon/user throttles continue to
        # apply on top.
        "contact": None if DEBUG else "5/minute",
        "payment": None if DEBUG else "10/minute",
        "payment_anon": None if DEBUG else "5/minute",
        "cart_mutation": None if DEBUG else "60/minute",
        "cart_mutation_anon": None if DEBUG else "30/minute",
        "search": None if DEBUG else "120/minute",
    },
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "core.filters.camel_case_ordering.CamelCaseOrderingFilter",
        "rest_framework.filters.SearchFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "core.api.schema.AutoSchema",
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
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
SOCIALACCOUNT_EMAIL_VERIFICATION = "mandatory"
SOCIALACCOUNT_REQUESTS_TIMEOUT = 10
SOCIALACCOUNT_PROVIDERS = {
    "github": {
        "APP": {
            "client_id": getenv("SOCIALACCOUNT_GITHUB_CLIENT_ID", ""),
            "secret": getenv("SOCIALACCOUNT_GITHUB_SECRET", ""),
            "key": "",
        },
        "SCOPE": ["read:user", "user:email"],
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

ACCOUNT_CHANGE_EMAIL = getenv("ACCOUNT_CHANGE_EMAIL", "True") == "True"
ACCOUNT_USER_MODEL_USERNAME_FIELD = "username"
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_NOTIFICATIONS = True
ACCOUNT_USERNAME_MIN_LENGTH = 2
ACCOUNT_USERNAME_MAX_LENGTH = 30
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_ADAPTER = "tenant.allauth_adapter.TenantAccountAdapter"
ACCOUNT_SIGNUP_REDIRECT_URL = NUXT_BASE_URL + "/account"
ACCOUNT_LOGIN_BY_CODE_ENABLED = True
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = True
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_TIMEOUT = 300
ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS = 3
ACCOUNT_LOGIN_BY_CODE_TIMEOUT = 300
ALLAUTH_USER_CODE_FORMAT = {"numeric": True, "dashed": False, "length": 6}
ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD = "email_confirm"
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "http" if DEBUG else "https"
ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE = True
ACCOUNT_EMAIL_SUBJECT_PREFIX = f"[{SITE_NAME}] "

LOGIN_REDIRECT_URL = NUXT_BASE_URL + "/account"
USERSESSIONS_TRACK_ACTIVITY = True

# Client IP resolution for allauth's session/rate-limit machinery is handled
# by UserAccountAdapter.get_client_ip, which prefers X-Real-IP (set by the
# Nuxt proxy from h3 getRequestIP) and falls back to REMOTE_ADDR. We override
# the adapter instead of setting ALLAUTH_TRUSTED_CLIENT_IP_HEADER because the
# latter hard-requires the header — direct-to-Django paths (health probes,
# Celery-triggered HTTP calls, tests) would otherwise 403 on every hit.

HEADLESS_TOKEN_STRATEGY = "core.api.tokens.SessionTokenStrategy"
HEADLESS_FRONTEND_URLS = {
    "account_confirm_email": f"{NUXT_BASE_URL}/account/verify-email/{{key}}",
    "account_reset_password": f"{NUXT_BASE_URL}/account/password/reset",
    "account_reset_password_from_key": f"{NUXT_BASE_URL}/account/password/reset/key/{{key}}",
    "account_signup": f"{NUXT_BASE_URL}/account/signup",
    "socialaccount_login_error": f"{NUXT_BASE_URL}/account/provider/callback",
}

USE_AWS = getenv("USE_AWS", "False") == "True"

REDIS_HOST = getenv("REDIS_HOST", "localhost")
REDIS_PORT = getenv("REDIS_PORT", "6379")
REDIS_PASSWORD = getenv("REDIS_PASSWORD", "")
REDIS_URL = (
    f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/0"
    if REDIS_PASSWORD
    else f"redis://{REDIS_HOST}:{REDIS_PORT}/0"
)

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
        "KEY_FUNCTION": "tenant.cache.make_tenant_key",
    },
}

CACHE_CLEAR_PREFIXES: list[str] = [
    f"{DEFAULT_CACHE_KEY_PREFIX}:{DEFAULT_CACHE_VERSION}:",  # Django: "redis:1:"
    "cache:",  # Nuxt SSR cache
]

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
CELERY_ENABLE_UTC = True
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
            else SCHEDULE_PRESETS["every_hour"],
        },
        "scheduled-database-backup": {
            "task": "core.tasks.scheduled_database_backup",
            "schedule": SCHEDULE_PRESETS["daily_3am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_hour"],
        },
        "cleanup-old-backups": {
            "task": "core.tasks.cleanup_old_backups",
            "schedule": SCHEDULE_PRESETS["weekly_sunday_5am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_hour"],
            "kwargs": {"days": 30, "backup_dir": "backups"},
        },
        "clear-duplicate-history": {
            "task": "tenant.tasks.fanout_clear_duplicate_history",
            "schedule": SCHEDULE_PRESETS["daily_5am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_hour"],
        },
        "clear-old-history": {
            "task": "tenant.tasks.fanout_clear_old_history",
            "schedule": SCHEDULE_PRESETS["weekly_sunday_3am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_hour"],
        },
        "clear-expired-sessions": {
            "task": "core.tasks.clear_expired_sessions_task",
            "schedule": SCHEDULE_PRESETS["weekly_monday_4am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_hour"],
        },
        "send-inactive-user-notifications": {
            "task": "tenant.tasks.fanout_send_inactive_user_notifications",
            "schedule": SCHEDULE_PRESETS["monthly_first_6am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_hour"],
        },
        "clear-all-cache": {
            "task": "core.tasks.clear_all_cache_task",
            "schedule": SCHEDULE_PRESETS["monthly_first_4am"]
            if not DEBUG
            else SCHEDULE_PRESETS["monthly_first_4am"],
        },
        "cleanup-abandoned-carts": {
            "task": "tenant.tasks.fanout_cleanup_abandoned_carts",
            "schedule": SCHEDULE_PRESETS["daily_4am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_hour"],
        },
        "cleanup-old-guest-carts": {
            "task": "tenant.tasks.fanout_cleanup_old_guest_carts",
            "schedule": SCHEDULE_PRESETS["daily_6am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_hour"],
        },
        "clear-expired-notifications": {
            "task": "tenant.tasks.fanout_clear_expired_notifications",
            "schedule": SCHEDULE_PRESETS["bimonthly_6am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_hour"],
        },
        "sync-meilisearch-indexes": {
            "task": "tenant.tasks.fanout_sync_meilisearch_indexes",
            "schedule": SCHEDULE_PRESETS["daily_2am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_hour"],
        },
        "cleanup-expired-stock-reservations": {
            "task": "tenant.tasks.fanout_cleanup_expired_stock_reservations",
            "schedule": SCHEDULE_PRESETS["every_hour"],
        },
        # TODO(multi-tenant): these came from main in the 2026-04 merge.
        # They currently run once against the public schema. Wrap them in
        # tenant.tasks.fanout_* variants so they iterate tenant schemas
        # like cleanup-expired-stock-reservations above.
        "check-pending-orders": {
            "task": "order.tasks.check_pending_orders",
            "schedule": SCHEDULE_PRESETS["daily_7am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_hour"],
        },
        "update-order-statuses-from-shipping": {
            "task": "order.tasks.update_order_statuses_from_shipping",
            "schedule": crontab(hour="*/6", minute="0")
            if not DEBUG
            else SCHEDULE_PRESETS["every_hour"],
        },
        "process-loyalty-points-expiration": {
            "task": "tenant.tasks.fanout_process_points_expiration",
            "schedule": SCHEDULE_PRESETS["daily_3am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_hour"],
        },
        "auto-cancel-stuck-pending-orders": {
            "task": "order.tasks.auto_cancel_stuck_pending_orders",
            "schedule": crontab(minute="*/15"),
        },
        "check-low-stock-products": {
            "task": "product.tasks.check_low_stock_products",
            "schedule": SCHEDULE_PRESETS["every_hour"],
        },
        "send-checkout-abandonment-emails": {
            "task": "order.tasks.send_checkout_abandonment_emails",
            "schedule": SCHEDULE_PRESETS["daily_6am"]
            if not DEBUG
            else SCHEDULE_PRESETS["every_hour"],
        },
    }

    if path.exists("/.dockerenv") and not getenv("KUBERNETES_SERVICE_HOST"):
        base_schedule.update(
            {
                "clear-development-logs": {
                    "task": "core.tasks.clear_development_log_files_task",
                    "schedule": SCHEDULE_PRESETS["daily_4am"]
                    if not DEBUG
                    else SCHEDULE_PRESETS["every_hour"],
                    "kwargs": {"days": 7},
                },
            }
        )

    return base_schedule


CELERY_BEAT_SCHEDULE = get_celery_beat_schedule()

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
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
# All origins allowed — django-tenants validates domains at middleware level.
# Each tenant domain is a distinct origin; static CORS list can't cover them.
CORS_ALLOW_ALL_ORIGINS = True
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
CSRF_COOKIE_DOMAIN = getenv("CSRF_COOKIE_DOMAIN") or None
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
SECURE_SSL_REDIRECT = (
    getenv("SECURE_SSL_REDIRECT", "False" if DEBUG else "True") == "True"
)
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https") if not DEBUG else None
)
_default_hsts = 0 if not DEBUG else 3600
SECURE_HSTS_SECONDS = int(getenv("SECURE_HSTS_SECONDS", str(_default_hsts)))
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0 and not DEBUG
SECURE_HSTS_PRELOAD = SECURE_HSTS_SECONDS > 0 and not DEBUG

# Currency
DEFAULT_CURRENCY = getenv("DEFAULT_CURRENCY", "EUR")
CURRENCIES = ("EUR", "USD")
CURRENCY_CHOICES = [("EUR", "EUR €"), ("USD", "USD $")]

CONN_HEALTH_CHECKS = True
ATOMIC_REQUESTS = False
INDEX_MAXIMUM_EXPR_COUNT = 8000
DATA_UPLOAD_MAX_NUMBER_FIELDS = 10000

# Use psycopg connection pool to bound DB connections per process. Each
# process maintains a pool of <DB_POOL_MAX_SIZE> connections regardless of
# the number of threads opened by the ASGI handler. Without this, asgiref's
# per-request ThreadPoolExecutor leaks Django thread-local connections that
# are only released when the worker thread is GC'd, which can exhaust
# Postgres max_connections under load.
DB_POOL_ENABLED = getenv("DB_POOL_ENABLED", "True") == "True"
DB_POOL_MIN_SIZE = int(getenv("DB_POOL_MIN_SIZE", "2"))
DB_POOL_MAX_SIZE = int(getenv("DB_POOL_MAX_SIZE", "8"))
DB_POOL_TIMEOUT = float(getenv("DB_POOL_TIMEOUT", "10"))

_db_options: dict = {
    "connect_timeout": 5,
    "options": "-c statement_timeout=30000 -c idle_in_transaction_session_timeout=10000",
}
if DB_POOL_ENABLED:
    _db_options["pool"] = {
        "min_size": DB_POOL_MIN_SIZE,
        "max_size": DB_POOL_MAX_SIZE,
        "timeout": DB_POOL_TIMEOUT,
    }

DATABASES = {
    "default": {
        "ATOMIC_REQUESTS": False,
        "AUTOCOMMIT": True,
        "CONN_HEALTH_CHECKS": True,
        # When OPTIONS["pool"] is set, the pool manages connection
        # lifetimes itself; Django requires CONN_MAX_AGE == 0 in this
        # mode (raises ImproperlyConfigured otherwise).
        "CONN_MAX_AGE": 0 if DB_POOL_ENABLED else 600,
        "TIME_ZONE": getenv("TIME_ZONE", "Europe/Athens"),
        "ENGINE": "django_tenants.postgresql_backend",
        "HOST": getenv("DB_HOST", "db"),
        "NAME": getenv("DB_NAME", "postgres"),
        "USER": getenv("DB_USER", "postgres"),
        "PASSWORD": getenv("DB_PASSWORD", "postgres"),
        "PORT": getenv("DB_PORT", "5432"),
        "OPTIONS": _db_options,
    },
}

DATABASE_ROUTERS = ["django_tenants.routers.TenantSyncRouter"]
TENANT_MODEL = "tenant.Tenant"
TENANT_DOMAIN_MODEL = "tenant.TenantDomain"
PUBLIC_SCHEMA_URLCONF = "tenant.urls_public"

if SYSTEM_ENV == "ci":
    DATABASES = {
        "default": {
            "ENGINE": "django_tenants.postgresql_backend",
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
    "ASYNC_INDEXING": getenv("MEILI_ASYNC_INDEXING", "False") == "True",
    "OFFLINE": getenv("MEILI_OFFLINE", "False") == "True",
    "DEFAULT_BATCH_SIZE": int(getenv("MEILI_DEFAULT_BATCH_SIZE", "5000")),
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
        "value": f"{API_BASE_URL}/api/v1/user/subscription/confirm/{{token}}",
    },
    {
        "name": "STOCK_RESERVATION_TTL_MINUTES",
        "type": "int",
        "value": 30,
    },
    {
        "name": "B2B_INVOICING_ENABLED",
        "type": "bool",
        "value": True,
    },
    {
        "name": "ORDER_AUTO_CANCEL_FAILED_PAYMENT_MINUTES",
        "type": "int",
        "value": 30,
    },
    {
        "name": "ORDER_AUTO_CANCEL_PENDING_HOURS",
        "type": "int",
        "value": 24,
    },
    {
        "name": "LOW_STOCK_THRESHOLD",
        "type": "int",
        "value": 10,
    },
    {
        "name": "CHECKOUT_ABANDONMENT_HOURS",
        "type": "int",
        "value": 2,
    },
    # Loyalty system settings
    {
        "name": "LOYALTY_ENABLED",
        "type": "bool",
        "value": False,
    },
    {
        "name": "LOYALTY_POINTS_FACTOR",
        "type": "decimal",
        "value": 1.0,
    },
    {
        "name": "LOYALTY_XP_PER_LEVEL",
        "type": "int",
        "value": 1000,
    },
    {
        "name": "LOYALTY_NEW_CUSTOMER_BONUS_ENABLED",
        "type": "bool",
        "value": False,
    },
    {
        "name": "LOYALTY_NEW_CUSTOMER_BONUS_POINTS",
        "type": "int",
        "value": 100,
    },
    {
        "name": "LOYALTY_REDEMPTION_RATIO_EUR",
        "type": "decimal",
        "value": 100.0,
    },
    {
        "name": "LOYALTY_REDEMPTION_RATIO_USD",
        "type": "decimal",
        "value": 100.0,
    },
    {
        "name": "LOYALTY_PRICE_BASIS",
        "type": "string",
        "value": "final_price",
    },
    {
        "name": "LOYALTY_POINTS_EXPIRATION_DAYS",
        "type": "int",
        "value": 0,
    },
    {
        "name": "LOYALTY_TIER_MULTIPLIER_ENABLED",
        "type": "bool",
        "value": False,
    },
    # Order reminder settings
    {
        "name": "PENDING_ORDER_REMINDER_MAX_COUNT",
        "type": "int",
        "value": 3,
    },
    {
        "name": "PENDING_ORDER_REMINDER_INTERVAL_DAYS_1",
        "type": "int",
        "value": 1,
    },
    {
        "name": "PENDING_ORDER_REMINDER_INTERVAL_DAYS_2",
        "type": "int",
        "value": 3,
    },
    {
        "name": "PENDING_ORDER_REMINDER_INTERVAL_DAYS_3",
        "type": "int",
        "value": 7,
    },
    # Re-engagement email settings
    {
        "name": "REENGAGEMENT_EMAIL_MAX_COUNT",
        "type": "int",
        "value": 3,
    },
    {
        "name": "REENGAGEMENT_EMAIL_COOLDOWN_DAYS",
        "type": "int",
        "value": 90,
    },
    {
        "name": "INACTIVE_USER_THRESHOLD_DAYS",
        "type": "int",
        "value": 60,
    },
    # Cart cleanup settings
    {
        "name": "ABANDONED_CART_CLEANUP_DAYS",
        "type": "int",
        "value": 7,
    },
    {
        "name": "OLD_GUEST_CART_CLEANUP_DAYS",
        "type": "int",
        "value": 30,
    },
    # Notification settings
    {
        "name": "NOTIFICATION_EXPIRATION_DAYS",
        "type": "int",
        "value": 180,
    },
    # Search settings
    {
        "name": "SEARCH_MAX_LIMIT",
        "type": "int",
        "value": 100,
    },
    # Invoice seller info — surfaced on the generated PDF via
    # ``order.invoicing._seller_snapshot``. Defaults land blank so
    # a fresh install doesn't silently ship a plausible-but-wrong
    # legal identity; ops must fill them in via the admin Settings
    # page before issuing the first invoice in production. Keys are
    # documented in ``INVOICE_SELLER_SETTING_KEYS``.
    {
        "name": "INVOICE_SELLER_NAME",
        "type": "string",
        "value": "",
        "description": "Legal company name shown at the top of the invoice.",
    },
    {
        "name": "INVOICE_SELLER_VAT_ID",
        "type": "string",
        "value": "",
        "description": "Seller tax/VAT identifier (ΑΦΜ in Greece). Required by Greek tax law.",
    },
    {
        "name": "INVOICE_SELLER_TAX_OFFICE",
        "type": "string",
        "value": "",
        "description": "Seller tax office (ΔΟΥ in Greece). Required by Greek tax law.",
    },
    {
        "name": "INVOICE_SELLER_REGISTRATION_NUMBER",
        "type": "string",
        "value": "",
        "description": "Business registry number (Αρ. Γ.Ε.ΜΗ. in Greece).",
    },
    {
        "name": "INVOICE_SELLER_BUSINESS_ACTIVITY",
        "type": "string",
        "value": "",
        "description": "Primary business activity / trade (Επάγγελμα in Greece).",
    },
    {
        "name": "INVOICE_SELLER_ADDRESS_LINE_1",
        "type": "string",
        "value": "",
        "description": "First line of the seller's registered address.",
    },
    {
        "name": "INVOICE_SELLER_ADDRESS_LINE_2",
        "type": "string",
        "value": "",
        "description": "Second line of the seller's registered address (optional).",
    },
    {
        "name": "INVOICE_SELLER_CITY",
        "type": "string",
        "value": "",
        "description": "City of the seller's registered address.",
    },
    {
        "name": "INVOICE_SELLER_POSTAL_CODE",
        "type": "string",
        "value": "",
        "description": "Postal code of the seller's registered address.",
    },
    {
        "name": "INVOICE_SELLER_COUNTRY",
        "type": "string",
        "value": "",
        "description": "Country of the seller's registered address.",
    },
    {
        "name": "INVOICE_SELLER_PHONE",
        "type": "string",
        "value": "",
        "description": "Seller contact phone number (optional).",
    },
    {
        "name": "INVOICE_SELLER_EMAIL",
        "type": "string",
        "value": "",
        "description": "Seller contact email (falls back to INFO_EMAIL).",
    },
    # myDATA (AADE / IAPR) integration. Credentials come from the
    # AADE developer portal after subscription approval — separate
    # accounts for dev and prod. Keep ``MYDATA_ENABLED`` off and
    # ``MYDATA_ENVIRONMENT=dev`` until a real submission round-trips
    # successfully against the sandbox.
    {
        "name": "MYDATA_ENABLED",
        "type": "bool",
        "value": False,
        "description": "Master toggle. When off the integration is a pure no-op.",
    },
    {
        "name": "MYDATA_AUTO_SUBMIT",
        "type": "bool",
        "value": False,
        "description": "If true, submit the invoice automatically once the PDF is rendered. Otherwise submission happens only via the admin action.",
    },
    {
        "name": "MYDATA_ENVIRONMENT",
        "type": "string",
        "value": "dev",
        "description": "Target environment: 'dev' (mydataapidev.aade.gr) or 'prod' (mydatapi.aade.gr/myDATA). Dev and prod are separate portals with separate credentials.",
    },
    {
        "name": "MYDATA_USER_ID",
        "type": "string",
        "value": "",
        "description": "aade-user-id header — TAXISnet developer account username issued via the myDATA portal.",
    },
    {
        "name": "MYDATA_SUBSCRIPTION_KEY",
        "type": "string",
        "value": "",
        "description": "Ocp-Apim-Subscription-Key header — Azure APIM key from the myDATA developer portal.",
    },
    {
        "name": "MYDATA_INVOICE_SERIES_PREFIX",
        "type": "string",
        "value": "GRVP",
        "description": "Prefix for the AADE series code. Combined with the issuer VAT + year to form e.g. 'GRVP-2026'.",
    },
    {
        "name": "MYDATA_ISSUER_BRANCH",
        "type": "int",
        "value": 0,
        "description": "AADE branch code — 0 for main branch. Change only if invoicing from a registered secondary establishment.",
    },
    {
        "name": "MYDATA_REQUEST_TIMEOUT_SECONDS",
        "type": "int",
        "value": 30,
        "description": "HTTP request timeout in seconds. AADE endpoints are sometimes slow under load — 30s is the usual sweet spot.",
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
    "TOKEN_TTL": datetime.timedelta(days=7),
    "AUTH_HEADER_PREFIX": "Bearer",
    "AUTO_REFRESH": True,
    "MIN_REFRESH_INTERVAL": 86400,  # Only extend TTL if token is >1 day old
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
_PARLER_LANG_TUPLE = (
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
)
PARLER_LANGUAGES = {
    SITE_ID: _PARLER_LANG_TUPLE,
    None: _PARLER_LANG_TUPLE,
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

# Rosetta Translation Management
ROSETTA_MESSAGES_PER_PAGE = 25
ROSETTA_ENABLE_TRANSLATION_SUGGESTIONS = True
ROSETTA_SHOW_AT_ADMIN_PANEL = False
ROSETTA_REQUIRES_AUTH = True
ROSETTA_WSGI_AUTO_RELOAD = False
ROSETTA_UWSGI_AUTO_RELOAD = False
ROSETTA_AUTO_COMPILE = True
ROSETTA_POFILE_WRAP_WIDTH = 0
ROSETTA_CACHE_NAME = "default"
ROSETTA_STORAGE_CLASS = "core.rosetta_storage.CacheClearingRosettaStorage"

UNFOLD = {
    "SITE_TITLE": getenv("UNFOLD_SITE_TITLE", "Webside Admin"),
    "SITE_HEADER": getenv("UNFOLD_SITE_HEADER", "Webside"),
    "SITE_SUBHEADER": getenv("UNFOLD_SITE_SUBHEADER", "Commerce control"),
    "SITE_SYMBOL": "storefront",
    "SITE_URL": "/",
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
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "SHOW_BACK_BUTTON": True,
    "BORDER_RADIUS": "0.625rem",
    "ENVIRONMENT": "admin.environment.environment_callback",
    "COLORS": {
        "base": {
            "50": "oklch(98.5% 0.002 247.839)",
            "100": "oklch(96.7% 0.003 264.542)",
            "200": "oklch(92.8% 0.006 264.531)",
            "300": "oklch(87.2% 0.010 258.338)",
            "400": "oklch(70.7% 0.022 261.325)",
            "500": "oklch(55.1% 0.027 264.364)",
            "600": "oklch(44.6% 0.030 256.802)",
            "700": "oklch(37.3% 0.034 259.733)",
            "800": "oklch(27.8% 0.033 256.848)",
            "900": "oklch(21.0% 0.034 264.665)",
            "950": "oklch(13.0% 0.028 261.692)",
        },
        "primary": {
            "50": "oklch(96.2% 0.018 272.314)",
            "100": "oklch(93.0% 0.034 272.788)",
            "200": "oklch(87.0% 0.065 274.039)",
            "300": "oklch(78.5% 0.115 274.713)",
            "400": "oklch(67.3% 0.182 276.935)",
            "500": "oklch(58.5% 0.233 277.117)",
            "600": "oklch(51.1% 0.262 276.966)",
            "700": "oklch(45.7% 0.240 277.023)",
            "800": "oklch(39.8% 0.195 277.366)",
            "900": "oklch(35.9% 0.144 278.697)",
            "950": "oklch(25.7% 0.090 281.288)",
        },
        "font": {
            "subtle-light": "var(--color-base-500)",
            "subtle-dark": "var(--color-base-400)",
            "default-light": "var(--color-base-700)",
            "default-dark": "var(--color-base-200)",
            "important-light": "var(--color-base-900)",
            "important-dark": "var(--color-base-50)",
        },
    },
    "SHOW_LANGUAGES": True,
    "LOGIN": {
        "redirect_after": lambda request: reverse_lazy("admin:index"),
    },
    "STYLES": [
        lambda request: static("css/styles.css"),
        lambda request: static("css/admin.css"),
        "https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200",
    ],
    "DASHBOARD_CALLBACK": "admin.dashboard.dashboard_callback",
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": _("Navigation"),
                "separator": True,
                "items": [
                    {
                        "title": _("Dashboard"),
                        "icon": "dashboard",
                        "link": reverse_lazy("admin:index"),
                    },
                ],
            },
            {
                "title": _("Catalog"),
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": _("Products"),
                        "icon": "inventory_2",
                        "link": reverse_lazy(
                            "admin:product_product_changelist"
                        ),
                    },
                    {
                        "title": _("Categories"),
                        "icon": "category",
                        "link": reverse_lazy(
                            "admin:product_productcategory_changelist"
                        ),
                    },
                    {
                        "title": _("Reviews"),
                        "icon": "star_rate",
                        "link": reverse_lazy(
                            "admin:product_productreview_changelist"
                        ),
                        "badge": "admin.badges.pending_reviews_badge",
                    },
                    {
                        "title": _("Tags"),
                        "icon": "label",
                        "link": reverse_lazy("admin:tag_tag_changelist"),
                    },
                    {
                        "title": _("Attributes"),
                        "icon": "tune",
                        "link": reverse_lazy(
                            "admin:product_attribute_changelist"
                        ),
                    },
                    {
                        "title": _("Attribute Values"),
                        "icon": "format_list_bulleted",
                        "link": reverse_lazy(
                            "admin:product_attributevalue_changelist"
                        ),
                    },
                ],
            },
            {
                "title": _("Sales"),
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": _("Orders"),
                        "icon": "receipt_long",
                        "link": reverse_lazy("admin:order_order_changelist"),
                        "badge": "admin.badges.pending_orders_badge",
                    },
                    {
                        "title": _("Carts"),
                        "icon": "shopping_cart",
                        "link": reverse_lazy("admin:cart_cart_changelist"),
                    },
                    {
                        "title": _("Payment Methods"),
                        "icon": "payments",
                        "link": reverse_lazy("admin:pay_way_payway_changelist"),
                    },
                    {
                        "title": _("VAT Rates"),
                        "icon": "percent",
                        "link": reverse_lazy("admin:vat_vat_changelist"),
                    },
                ],
            },
            {
                "title": _("Users & Engagement"),
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": _("Users"),
                        "icon": "people",
                        "link": reverse_lazy(
                            "admin:user_useraccount_changelist"
                        ),
                    },
                    {
                        "title": _("Subscriptions"),
                        "icon": "mail",
                        "link": reverse_lazy(
                            "admin:user_usersubscription_changelist"
                        ),
                    },
                    {
                        "title": _("Subscription Topics"),
                        "icon": "topic",
                        "link": reverse_lazy(
                            "admin:user_subscriptiontopic_changelist"
                        ),
                    },
                    {
                        "title": _("Groups"),
                        "icon": "shield_person",
                        "link": reverse_lazy("admin:auth_group_changelist"),
                    },
                ],
            },
            {
                "title": _("Blog & Content"),
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": _("Blog Posts"),
                        "icon": "article",
                        "link": reverse_lazy("admin:blog_blogpost_changelist"),
                    },
                    {
                        "title": _("Blog Authors"),
                        "icon": "edit_note",
                        "link": reverse_lazy(
                            "admin:blog_blogauthor_changelist"
                        ),
                    },
                    {
                        "title": _("Blog Categories"),
                        "icon": "folder_open",
                        "link": reverse_lazy(
                            "admin:blog_blogcategory_changelist"
                        ),
                    },
                    {
                        "title": _("Blog Comments"),
                        "icon": "chat_bubble",
                        "link": reverse_lazy(
                            "admin:blog_blogcomment_changelist"
                        ),
                        "badge": "admin.badges.pending_comments_badge",
                    },
                    {
                        "title": _("Blog Tags"),
                        "icon": "tag",
                        "link": reverse_lazy("admin:blog_blogtag_changelist"),
                    },
                ],
            },
            {
                "title": _("Communications"),
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": _("Notifications"),
                        "icon": "notifications_active",
                        "link": reverse_lazy(
                            "admin:notification_notification_changelist"
                        ),
                    },
                    {
                        "title": _("Contact Messages"),
                        "icon": "contact_mail",
                        "link": reverse_lazy(
                            "admin:contact_contact_changelist"
                        ),
                        "badge": "admin.badges.unread_messages_badge",
                    },
                ],
            },
            {
                "title": _("Loyalty"),
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": _("Loyalty Tiers"),
                        "icon": "workspace_premium",
                        "link": reverse_lazy(
                            "admin:loyalty_loyaltytier_changelist"
                        ),
                    },
                    {
                        "title": _("Points Transactions"),
                        "icon": "account_balance_wallet",
                        "link": reverse_lazy(
                            "admin:loyalty_pointstransaction_changelist"
                        ),
                    },
                ],
            },
            {
                "title": _("System Settings"),
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": _("Countries"),
                        "icon": "public",
                        "link": reverse_lazy(
                            "admin:country_country_changelist"
                        ),
                    },
                    {
                        "title": _("Regions"),
                        "icon": "map",
                        "link": reverse_lazy("admin:region_region_changelist"),
                    },
                    {
                        "title": _("Sites"),
                        "icon": "language",
                        "link": reverse_lazy("admin:sites_site_changelist"),
                    },
                    {
                        "title": _("Extra Settings"),
                        "icon": "settings",
                        "link": reverse_lazy(
                            "admin:extra_settings_setting_changelist"
                        ),
                    },
                ],
            },
        ],
    },
    "SITE_DROPDOWN": [
        {
            "icon": "translate",
            "title": _("Rosetta"),
            "link": reverse_lazy("rosetta-file-list-redirect"),
        },
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
SESSION_COOKIE_DOMAIN = getenv("SESSION_COOKIE_DOMAIN") or None
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_PATH = "/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_SAVE_EVERY_REQUEST = False
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
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
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"]
    if DEBUG
    else ["rest_framework.permissions.IsAuthenticated"],
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
        # Both ``SubscriptionTopic.category`` and ``Notification.category``
        # expose a ``category`` field, and they use different enum sets
        # (SubscriptionTopic.TopicCategory vs NotificationCategoryEnum).
        # Without these overrides, drf-spectacular auto-renames the
        # colliding enum to ``CategoryXYZEnum`` which bleeds into
        # generated frontend types and breaks consumer imports on every
        # regeneration.
        "TopicCategory": "user.models.subscription.SubscriptionTopic.TopicCategory",
        "NotificationCategory": "notification.enum.NotificationCategoryEnum",
        # ``Order.document_type`` (6 values — includes fulfilment
        # documents like shipping labels / credit notes) and the
        # creation-time subset (``OrderCreateFromCartSerializer`` —
        # only RECEIPT/INVOICE) share the ``documentType`` field name.
        # Without these overrides, drf-spectacular auto-renames the
        # collider to ``DocumentType128Enum`` which bleeds into
        # generated frontend types on every regeneration.
        "OrderDocumentType": "order.enum.document_type.OrderDocumentTypeEnum",
        "OrderCreateDocumentType": "order.enum.document_type.OrderCreateDocumentTypeEnum",
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
            "BACKEND": "tenant.storage.TenantFileSystemStorage",
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
            "BACKEND": "tenant.storage.TenantFileSystemStorage",
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

BLOG_COMMENT_AUTO_APPROVE = (
    getenv("BLOG_COMMENT_AUTO_APPROVE", "True") == "True"
)

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
                "format": '{"timestamp": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "module": "%(module)s", "function": "%(funcName)s", "line": %(lineno)d, "process": "%(process)d", "thread": "%(thread)d", "pod": "%(hostname)s", "correlation_id": "%(correlation_id)s", "message": "%(message)s"}',
                "datefmt": "%Y-%m-%dT%H:%M:%S",
            },
            "console": {
                "format": "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] [cid=%(correlation_id)s] %(funcName)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "filters": {
            "add_hostname": {
                "()": "core.logging.HostnameFilter",
            },
            "add_correlation_id": {
                "()": "core.logging.CorrelationIdFilter",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "json"
                if SYSTEM_ENV == "production"
                else "console",
                "level": logging_level,
                "filters": ["add_hostname", "add_correlation_id"],
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
                "format": "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] [cid=%(correlation_id)s] %(funcName)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "simple": {
                "format": "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
                "datefmt": "%H:%M:%S",
            },
        },
        "filters": {
            "add_correlation_id": {
                "()": "core.logging.CorrelationIdFilter",
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
                "filters": ["add_correlation_id"],
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
DJSTRIPE_WEBHOOK_SECRET = getenv("DJSTRIPE_WEBHOOK_SECRET", "")
if not DJSTRIPE_WEBHOOK_SECRET or DJSTRIPE_WEBHOOK_SECRET == "whsec_...":
    if SYSTEM_ENV == "production":
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(
            "DJSTRIPE_WEBHOOK_SECRET must be set to a real Stripe webhook "
            "secret in production (DJSTRIPE_WEBHOOK_VALIDATION=verify_signature)."
        )
    DJSTRIPE_WEBHOOK_SECRET = "whsec_dev_placeholder_not_used_for_verification"

# Pin the Stripe API version so library upgrades can't silently shift
# webhook payload shapes or idempotency keys. Update this in lockstep
# with the version configured in the Stripe Dashboard. The dj-stripe
# docs name this setting STRIPE_API_VERSION (not DJSTRIPE_-prefixed).
STRIPE_API_VERSION = getenv("STRIPE_API_VERSION", "2024-04-10")
STRIPE_WEBHOOK_DEBUG = getenv("STRIPE_WEBHOOK_DEBUG", "false").lower() == "true"

# Viva Wallet Configuration
VIVA_WALLET_MERCHANT_ID = getenv("VIVA_WALLET_MERCHANT_ID", "")
VIVA_WALLET_API_KEY = getenv("VIVA_WALLET_API_KEY", "")
VIVA_WALLET_CLIENT_ID = getenv("VIVA_WALLET_CLIENT_ID", "")
VIVA_WALLET_CLIENT_SECRET = getenv("VIVA_WALLET_CLIENT_SECRET", "")
VIVA_WALLET_SOURCE_CODE = getenv("VIVA_WALLET_SOURCE_CODE", "Default")
VIVA_WALLET_LIVE_MODE = getenv(
    "VIVA_WALLET_LIVE_MODE", str(not DEBUG)
).lower() in (
    "true",
    "1",
    "yes",
)
VIVA_WALLET_WEBHOOK_VERIFICATION_KEY = getenv(
    "VIVA_WALLET_WEBHOOK_VERIFICATION_KEY", ""
)


# SHIPPING SETTINGS
FEDEX_API_KEY = getenv("FEDEX_API_KEY", "")
FEDEX_ACCOUNT_NUMBER = getenv("FEDEX_ACCOUNT_NUMBER", "")
UPS_API_KEY = getenv("UPS_API_KEY", "")
UPS_ACCOUNT_NUMBER = getenv("UPS_ACCOUNT_NUMBER", "")
