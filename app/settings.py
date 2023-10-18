import sys
from os import getenv
from os import path
from pathlib import Path

import dotenv
from django.core.management.utils import get_random_secret_key
from django.utils.translation import gettext_lazy as _

from core.utils.cache import CustomCacheConfig

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables
dotenv_file = BASE_DIR / ".env"

if path.isfile(dotenv_file):
    dotenv.load_dotenv(dotenv_file)

DEVELOPMENT_MODE = getenv("DEVELOPMENT_MODE", "False") == "True"

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = getenv("DJANGO_SECRET_KEY", get_random_secret_key())

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = getenv("DEBUG", "True") == "True"
SYSTEM_ENV = getenv("SYSTEM_ENV", "dev")

# Security
SECURE_SSL_REDIRECT = False if DEBUG else True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if DEBUG else None
SECURE_HSTS_SECONDS = 0 if DEBUG else 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = False if DEBUG else True
SECURE_HSTS_PRELOAD = False if DEBUG else True

if "celery" in sys.argv[0]:
    DEBUG = False

APP_BASE_URL = getenv("APP_BASE_URL", "http://localhost:8000")
NUXT_BASE_URL = getenv("NUXT_BASE_URL", "http://localhost:3000")
NUXT_BASE_DOMAIN = getenv("NUXT_BASE_DOMAIN", "localhost:3000")
MEDIA_STREAM_BASE_URL = getenv("MEDIA_STREAM_BASE_URL", "http://localhost:3003")
MEDIA_STREAM_PATH = getenv(
    "MEDIA_STREAM_PATH", "http://localhost:3003/media_stream-image"
)
APP_MAIN_HOST_NAME = getenv("APP_MAIN_HOST_NAME", "localhost")

ALLOWED_HOSTS = [
    APP_MAIN_HOST_NAME,
    NUXT_BASE_URL,
    MEDIA_STREAM_BASE_URL,
    "localhost",
    "127.0.0.1",
    "backend",
]
ALLOWED_HOSTS.extend(
    filter(
        None,
        getenv("ALLOWED_HOSTS", "[*]").split(","),
    )
)

CORS_EXPOSE_HEADERS = ["Content-Type", "X-CSRFToken"]
CSRF_TRUSTED_ORIGINS = [
    APP_BASE_URL,
    NUXT_BASE_URL,
    MEDIA_STREAM_BASE_URL,
]
CORS_ALLOWED_ORIGINS = [
    APP_BASE_URL,
    NUXT_BASE_URL,
    MEDIA_STREAM_BASE_URL,
]
CORS_ORIGIN_ALLOW_ALL = getenv("CORS_ORIGIN_ALLOW_ALL", "True") == "True"
CORS_ALLOW_CREDENTIALS = True
CORS_ORIGIN_WHITELIST = [
    APP_BASE_URL,
    NUXT_BASE_URL,
    MEDIA_STREAM_BASE_URL,
]
INTERNAL_IPS = [
    "127.0.0.1",
]

if DEBUG:
    import socket  # only if you haven't already imported this

    hostname, aliaslist, ips = socket.gethostbyname_ex(socket.gethostname())
    INTERNAL_IPS = [ip[: ip.rfind(".")] + ".1" for ip in ips] + [
        "127.0.0.1",
        "10.0.2.2",
    ]

custom_cache_config = CustomCacheConfig()

# Application definition
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
INSTALLED_APPS = DJANGO_APPS + PROJECT_APPS + THIRD_PARTY_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "session.middleware.SessionTraceMiddleware",
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

# Site info
SITE_NAME = getenv("SITE_NAME", "Django")
SITE_ID = 2

# Slash append
APPEND_SLASH = getenv("APPEND_SLASH", "False") == "True"

# User model
AUTH_USER_MODEL = "user.UserAccount"

# Sessions and Cookies
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_COOKIE_SAMESITE = "Strict"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = False if DEBUG else True
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

CSRF_COOKIE_SAMESITE = "Strict"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = True

CORS_ALLOW_METHODS = [
    "DELETE",
    "GET",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
]

DATA_UPLOAD_MAX_NUMBER_FIELDS = 22500  # higher than the count of fields

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

REST_AUTH = {
    "LOGIN_SERIALIZER": "authentication.serializers.AuthenticationLoginSerializer",
    "TOKEN_SERIALIZER": "authentication.serializers.AuthenticationTokenSerializer",
    "JWT_SERIALIZER": "authentication.serializers.AuthenticationJWTSerializer",
    "JWT_SERIALIZER_WITH_EXPIRATION": "authentication.serializers.AuthenticationJWTSerializerWithExpiration",
    "JWT_TOKEN_CLAIMS_SERIALIZER": "authentication.serializers.AuthenticationTokenObtainPairSerializer",
    "USER_DETAILS_SERIALIZER": "authentication.serializers.AuthenticationSerializer",
    "PASSWORD_RESET_SERIALIZER": "authentication.serializers.AuthenticationPasswordResetSerializer",
    "PASSWORD_RESET_CONFIRM_SERIALIZER": "authentication.serializers.AuthenticationPasswordResetConfirmSerializer",
    "PASSWORD_CHANGE_SERIALIZER": "authentication.serializers.AuthenticationPasswordChangeSerializer",
    "REGISTER_SERIALIZER": "authentication.serializers.AuthenticationRegisterSerializer",
    "REGISTER_PERMISSION_CLASSES": ("rest_framework.permissions.AllowAny",),
    "TOKEN_MODEL": "rest_framework.authtoken.models.Token",
    "TOKEN_CREATOR": "dj_rest_auth.utils.default_create_token",
    "PASSWORD_RESET_USE_SITES_DOMAIN": False,
    "OLD_PASSWORD_FIELD_ENABLED": False,
    "LOGOUT_ON_PASSWORD_CHANGE": False,
    "SESSION_LOGIN": False,
    "USE_JWT": True,
    "JWT_AUTH_COOKIE": "jwt_auth",
    "JWT_AUTH_REFRESH_COOKIE": "jwt_refresh_auth",
    "JWT_AUTH_REFRESH_COOKIE_PATH": "/",
    "JWT_AUTH_SECURE": False,
    "JWT_AUTH_HTTPONLY": False,
    "JWT_AUTH_SAMESITE": "Lax",
    "JWT_AUTH_RETURN_EXPIRATION": False,
    "JWT_AUTH_COOKIE_USE_CSRF": False,
    "JWT_AUTH_COOKIE_ENFORCE_CSRF_ON_UNAUTHENTICATED": False,
}

GOOGLE_CALLBACK_URL = getenv("GOOGLE_CALLBACK_URL", "http://localhost:8000")
SOCIALACCOUNT_ADAPTER = "authentication.views.social.SocialAccountAdapter"
SOCIALACCOUNT_PROVIDERS = {
    "facebook": {
        "METHOD": "oauth2",
        "SDK_URL": "//connect.facebook.net/{locale}/sdk.js",
        "SCOPE": ["email", "public_profile"],
        "AUTH_PARAMS": {"auth_type": "reauthenticate"},
        "INIT_PARAMS": {"cookie": True},
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
        "EXCHANGE_TOKEN": True,
        "VERIFIED_EMAIL": False if DEBUG else True,
        "LOCALE_FUNC": lambda request: "en_US",
        "VERSION": "v15.0",
        "GRAPH_API_URL": "https://graph.facebook.com/v15.0/",
    },
    "google": {
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
    },
}

ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_AUTHENTICATION_METHOD = "email"
ACCOUNT_ADAPTER = "user.adapter.UserAccountAdapter"
ACCOUNT_SIGNUP_REDIRECT_URL = NUXT_BASE_URL + "/account"
LOGIN_REDIRECT_URL = NUXT_BASE_URL + "/account"

# MFA
MFA_ADAPTER = "allauth.mfa.adapter.DefaultMFAAdapter"
MFA_RECOVERY_CODE_COUNT = 10
MFA_TOTP_PERIOD = 30
MFA_TOTP_DIGITS = 6

# WSGI
WSGI_APPLICATION = "app.wsgi.application"

# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": getenv("DB_HOST", "db"),
        "NAME": getenv("DB_NAME", "devdb"),
        "USER": getenv("DB_USER", "devuser"),
        "PASSWORD": getenv("DB_PASSWORD", "changeme"),
        "PORT": getenv("DB_PORT", "5432"),
    },
    "replica": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": getenv("DB_HOST_TEST", "db_replica"),
        "NAME": getenv("DB_NAME_TEST", "devdb_replica"),
        "TEST": {
            "MIRROR": getenv("DB_TEST_MIRROR", "default"),
        },
    },
}

if SYSTEM_ENV == "GITHUB_WORKFLOW":
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

if SYSTEM_ENV != "GITHUB_WORKFLOW":
    CACHES = {
        "default": custom_cache_config.cache_backend,
        "fallback": {
            "BACKEND": "django.core.cache.backends.dummy.DummyCache",
        },
    }

REDIS_HEALTHY = custom_cache_config.ready_healthy

# Celery
CELERY_BROKER_URL = getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = getenv("CELERY_RESULT_BACKEND", "django-db")
CELERY_CACHE_BACKEND = getenv("CELERY_CACHE_BACKEND", "django-cache")
CELERY_TASK_TRACK_STARTED = True
CELERY_ENABLE_UTC = False
CELERY_TIMEZONE = getenv("TIME_ZONE", "Europe/Athens")
CELERY_ACCEPT_CONTENT = ["application/json"]
CELERY_RESULT_SERIALIZER = "json"
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_EXTENDED = True
CELERY_TASK_RESULT_EXPIRES = 3600

# Celery Beat
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# Channels Configuration
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            # "hosts": [("redis", 6379)],
            "hosts": [("localhost", 6379)],
        },
    },
}

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

# Internationalization
LANGUAGE_CODE = getenv("LANGUAGE_CODE", "en")
TIME_ZONE = getenv("TIME_ZONE", "Europe/Athens")
USE_I18N = getenv("USE_I18N", "True") == "True"
USE_TZ = getenv("USE_TZ", "True") == "True"
LANGUAGES = [
    ("en", _("English")),
    ("de", _("German")),
    ("el", _("Greek")),
]
# Locales available path
LOCALE_PATHS = [path.join(BASE_DIR, "locale/")]

# Rosseta
ROSETTA_MESSAGES_PER_PAGE = 25
ROSETTA_ENABLE_TRANSLATION_SUGGESTIONS = True
ROSETTA_SHOW_AT_ADMIN_PANEL = True

# Currency
DEFAULT_CURRENCY = "EUR"
BASE_CURRENCY = "EUR"
CURRENCIES = ("USD", "EUR")
CURRENCY_CHOICES = [("USD", "USD $"), ("EUR", "EUR €")]

# Serialization
SERIALIZATION_MODULES = {"json": "djmoney.serializers"}

# Parler
PARLER_DEFAULT_LANGUAGE_CODE = "en"
PARLER_LANGUAGES = {
    # 1 is from the SITE_ID
    SITE_ID: (
        {
            "code": "en",
        },
        {
            "code": "de",
        },
        {
            "code": "el",
        },
    ),
    "default": {
        "fallbacks": ["en"],  # defaults to PARLER_DEFAULT_LANGUAGE_CODE
        "hide_untranslated": False,  # the default; let .active_translations() return fallbacks too.
    },
}
PARLER_ENABLE_CACHING = True

# DeepL
DEEPL_AUTH_KEY = getenv("DEEPL_AUTH_KEY", "changeme")

# Email Settings
EMAIL_BACKEND = getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = getenv("EMAIL_HOST", "localhost")
EMAIL_PORT = getenv("EMAIL_PORT", "25")
EMAIL_HOST_USER = getenv("EMAIL_HOST_USER", "localhost@gmail.com")
EMAIL_HOST_PASSWORD = getenv("EMAIL_HOST_PASSWORD", "changeme")
EMAIL_USE_TLS = getenv("EMAIL_USE_TLS", "False") == "True"
DEFAULT_FROM_EMAIL = getenv("DEFAULT_FROM_EMAIL", "localhost@gmail.com")

# Static files (CSS, JavaScript, Images)
if DEVELOPMENT_MODE is True or SYSTEM_ENV == "GITHUB_WORKFLOW":
    STATIC_URL = "static/"
    STATIC_ROOT = path.join(BASE_DIR, "static")
    MEDIA_URL = "media/"
    MEDIA_ROOT = path.join(BASE_DIR, "media")
    STATICFILES_DIRS = (BASE_DIR.joinpath("files"),)
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
        },
    }
else:
    AWS_S3_ACCESS_KEY_ID = getenv("AWS_S3_ACCESS_KEY_ID", "changeme")
    AWS_S3_SECRET_ACCESS_KEY = getenv("AWS_S3_SECRET_ACCESS_KEY", "changeme")
    AWS_STORAGE_BUCKET_NAME = getenv("AWS_STORAGE_BUCKET_NAME", "changeme")
    AWS_S3_REGION_NAME = getenv("AWS_S3_REGION_NAME", "changeme")
    AWS_S3_ENDPOINT_URL = f"https://{AWS_S3_REGION_NAME}.digitaloceanspaces.com"
    AWS_S3_OBJECT_PARAMETERS = {"CacheControl": "max-age=86400"}
    AWS_DEFAULT_ACL = "public-read"
    AWS_LOCATION = "static"
    AWS_MEDIA_LOCATION = "media"
    AWS_S3_CUSTOM_DOMAIN = getenv("AWS_S3_CUSTOM_DOMAIN", "changeme")
    STORAGES = {
        "default": {
            "BACKEND": "core.storages.CustomS3Boto3Storage",
        },
        "staticfiles": {
            "BACKEND": "storages.backends.s3boto3.S3StaticStorage",
        },
    }


# Tinymce admin panel editor config
TINYMCE_DEFAULT_CONFIG = {
    "theme": "silver",
    "height": 500,
    "width": 960,
    "menubar": "file edit view insert format tools table help",
    "plugins": "advlist,autolink,lists,link,image,charmap,print,preview,anchor,"
    "searchreplace,visualblocks,code,fullscreen,insertdatetime,media,table,paste,"
    "code,help,wordcount",
    "toolbar": "undo redo | formatselect | "
    "bold italic backcolor | alignleft aligncenter "
    "alignright alignjustify | bullist numlist outdent indent | "
    "removeformat | code | help",
}

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

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
        "anon": None if DEBUG else "1000/day",
        "user": None if DEBUG else "3000/day",
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

SPECTACULAR_SETTINGS = {
    "TITLE": getenv("DJANG0_SPECTACULAR_SETTINGS_TITLE", "Django"),
    "DESCRIPTION": getenv("DJANG0_SPECTACULAR_SETTINGS_DESCRIPTION", "Django"),
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

# logs
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "[%(asctime)s] %(levelname)s | %(funcName)s | %(name)s | %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
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
            "level": "ERROR",
            "filters": ["require_debug_true"],
            "class": "logging.FileHandler",
            "formatter": "simple",
            "filename": path.join(BASE_DIR, "logs/django.log"),
        },
        "console": {
            "level": "INFO",
            "filters": ["require_debug_true"],
            "class": "logging.StreamHandler",
            "stream": sys.stdout,
            "formatter": "verbose",
        },
        "logger": {
            "level": "DEBUG",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": path.join(BASE_DIR, "logs/django.log"),
            "formatter": "simple",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": True,
        },
        "signal": {
            "handlers": ["logger"],
            "level": "DEBUG",
        },
    },
}
