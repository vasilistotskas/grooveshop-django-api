import sys
from os import getenv
from os import path
from pathlib import Path

import dotenv
from django.core.management.utils import get_random_secret_key
from django.utils.translation import gettext_lazy as _

BASE_DIR = Path(__file__).resolve().parent.parent

dotenv_file = BASE_DIR / ".env"

if path.isfile(dotenv_file):
    dotenv.load_dotenv(dotenv_file)

SYSTEM_ENV = getenv("SYSTEM_ENV", "dev")

SECRET_KEY = getenv("DJANGO_SECRET_KEY", get_random_secret_key())

DEBUG = getenv("DEBUG", "True") == "True"

if SYSTEM_ENV not in ["docker", "production"] and "celery" in sys.argv[0]:
    dotenv.set_key(dotenv_file, "DEBUG", "False")

INTERNAL_IPS = [
    "127.0.0.1",
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

ALLOWED_HOSTS = []  # Start with an empty list

# Add any additional hosts from the environment variable
additional_hosts = getenv("ALLOWED_HOSTS", "*").split(",")
ALLOWED_HOSTS.extend(filter(None, additional_hosts))  # Filter out empty strings

# Django built-in apps
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.postgres",
]

# Project-specific apps
PROJECT_APPS = [
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
    "dj_rest_auth",
    "djmoney",
    "phonenumber_field",
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "dj_rest_auth.registration",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.facebook",
    "allauth.socialaccount.providers.google",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "django_otp.plugins.otp_hotp",
    "django_otp.plugins.otp_static",
    "django_celery_beat",
    "django_celery_results",
    "django_browser_reload",
]

# Combine all apps together for the INSTALLED_APPS setting
INSTALLED_APPS = DJANGO_APPS + PROJECT_APPS + THIRD_PARTY_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Reset login flow middleware. If this middleware is included, the login
    # flow is reset if another page is loaded between login and successfully
    # entering two-factor credentials.
    "allauth.account.middleware.AccountMiddleware",
    "djangorestframework_camel_case.middleware.CamelCaseMiddleWare",
    "django_browser_reload.middleware.BrowserReloadMiddleware",
    "core.middleware.timezone.TimezoneMiddleware",
]

ROOT_URLCONF = "app.urls"

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
            ],
        },
    },
]

AUTHENTICATION_BACKENDS = [
    # Needed to log in by username in Django admin, regardless of `allauth`
    "django.contrib.auth.backends.ModelBackend",
    # `allauth` specific authentication methods, such as login by e-mail
    "allauth.account.auth_backends.AuthenticationBackend",
]

WSGI_APPLICATION = "wsgi.application"

# User Model
AUTH_USER_MODEL = "user.UserAccount"

# Internationalization
LANGUAGE_CODE = getenv("LANGUAGE_CODE", "en")
TIME_ZONE = getenv("TIME_ZONE", "Europe/Athens")
USE_I18N = getenv("USE_I18N", "True") == "True"
USE_TZ = getenv("USE_TZ", "True") == "True"

# Site info
SITE_NAME = getenv("SITE_NAME", "Django")
SITE_ID = int(getenv("SITE_ID", 1))

LANGUAGES = [
    ("en", _("English")),
    ("de", _("German")),
    ("el", _("Greek")),
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
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.BasicAuthentication",
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
        "dj_rest_auth.jwt_auth.JWTCookieAuthentication",
    ],
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
        "anon": None if DEBUG else "10000/day",
        "user": None if DEBUG else "30000/day",
    },
    # Filtering
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    # Schema
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Pagination
    "DEFAULT_PAGINATION_CLASS": "core.pagination.count.CountPaginator",
    "PAGE_SIZE": 100,
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
    # Other
    "COERCE_DECIMAL_TO_STRING": False,
}

# Other general settings
APPEND_SLASH = getenv("APPEND_SLASH", "False") == "True"

DEEPL_AUTH_KEY = getenv("DEEPL_AUTH_KEY", "changeme")

LOCALE_PATHS = [path.join(BASE_DIR, "locale/")]

ENABLE_DEBUG_TOOLBAR = getenv("ENABLE_DEBUG_TOOLBAR", "False") == "True"

if ENABLE_DEBUG_TOOLBAR:
    INSTALLED_APPS += ["debug_toolbar"]
    MIDDLEWARE.append("debug_toolbar.middleware.DebugToolbarMiddleware")

    DEBUG_TOOLBAR_PANELS = [
        "debug_toolbar.panels.history.HistoryPanel",
        "debug_toolbar.panels.versions.VersionsPanel",
        "debug_toolbar.panels.timer.TimerPanel",
        "debug_toolbar.panels.settings.SettingsPanel",
        "debug_toolbar.panels.headers.HeadersPanel",
        "debug_toolbar.panels.request.RequestPanel",
        "debug_toolbar.panels.sql.SQLPanel",
        "debug_toolbar.panels.staticfiles.StaticFilesPanel",
        "debug_toolbar.panels.templates.TemplatesPanel",
        "debug_toolbar.panels.cache.CachePanel",
        "debug_toolbar.panels.signals.SignalsPanel",
        "debug_toolbar.panels.redirects.RedirectsPanel",
        "debug_toolbar.panels.profiling.ProfilingPanel",
    ]
    DEBUG_TOOLBAR_CONFIG = {"INTERCEPT_REDIRECTS": False, "RESULTS_CACHE_SIZE": 100}
