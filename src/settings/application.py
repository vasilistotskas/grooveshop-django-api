"""The installed apps, middleware, URL root, templates and site.

Not ``apps``: an ``apps.py`` is a Django app's configuration."""

from os import getenv, path

from settings.env import DEBUG, SRC_DIR

SERIALIZATION_MODULES = {"json": "djmoney.serializers"}

# ─── Multi-tenancy: SHARED vs TENANT apps ──────────────────
# SHARED_APPS live in the public schema (platform infrastructure + global data).
# TENANT_APPS live in each tenant schema (store-specific data).
# Apps in BOTH must appear in both lists (e.g. contenttypes, auth).

SHARED_APPS = [
    "django_tenants",
    "tenant",
    # Django core (platform admin in public schema)
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
    # rest_framework.authtoken is intentionally NOT installed — auth is
    # Knox (BoundedTokenAuthentication) + session; the DRF token app was
    # unused and only widened the schema-endpoint auth surface (G0358).
    "drf_spectacular",
    "rosetta",
    "core",
    "devtools",
    # Celery infrastructure — beat scheduler runs in public schema
    # (the scheduler reads PeriodicTask from public).
    #
    # Task results are PUBLIC-SCHEMA ONLY and deliberately not mirrored
    # into TENANT_APPS. Only failures are ever written (see
    # CELERY_TASK_IGNORE_RESULT in settings/celery.py), and a failure is
    # platform operations, not one store's data: it belongs where the control
    # plane can read every store's in one list, and where
    # ``celery.backend_cleanup`` — which beat dispatches with no tenant
    # header, so it runs in public — can actually prune it. Per-tenant
    # tables would be written by tenant-context tasks, invisible to the
    # control plane, and never cleaned. tenant/migrations/0039 drops the
    # ones an earlier TENANT_APPS entry created.
    "scheduler",
    "django_celery_results",
    # Platform-wide Setting table. Also in TENANT_APPS so per-tenant
    # settings can override.
    "extra_settings",
    # Global reference data (identical across ALL tenants)
    "country",
    "region",
    # User model must be in SHARED for AUTH_USER_MODEL FK resolution
    # in public-schema auth migrations. Tenant schemas have their own copy.
    "user",
    # Email platform: a store's switches and opt-outs live in its schema,
    # the platform's own in public (one model, ``docs/emails.md``).
    "emails",
    # Session tracking rows must exist where the session's USER row
    # lives: platform staff are public-schema identities, so their
    # UserSession rows are written to the public schema (see
    # tenant.middleware.TenantAwareUserSessionsMiddleware) — without
    # this app in SHARED, a fresh (non-clone) database has no
    # usersessions table in public and every platform-admin request
    # crashes. Also in TENANT_APPS for per-schema customer sessions.
    "allauth.usersessions",
    # The second factor of a platform superuser's admin login
    # (``admin.staff_mfa``): the authenticator rows must exist where the
    # user row lives, which for platform staff is the public schema. Also
    # in TENANT_APPS for customers' own authenticators.
    "allauth.mfa",
    # The staff issuer: on the public schema the OIDC provider connects a
    # store's STAFF (platform identities) to an AI app, for the store-admin
    # tools on ``/mcp/admin``; its clients and tokens live where those users
    # do. Also in TENANT_APPS, where each store's own issuer links its
    # CUSTOMERS. Two token tables, so neither kind is ever found where the
    # other is checked (``agent/staff.py``).
    "allauth.idp.oidc",
]

TENANT_APPS = [
    # Required in BOTH by django-tenants
    "django.contrib.contenttypes",
    "django.contrib.auth",
    # User & Auth (isolated per-tenant)
    #
    # Sessions must be per-schema for the same reason ``user`` is.
    # ``_auth_user_id`` is resolved against whatever schema serves the
    # request, so ONE shared session table means a cookie minted on one
    # host is accepted on another and resolved against a different
    # user table — where the same id is a different person. The cookie
    # Domain is not a boundary: TenantCookieDomainMiddleware scopes it
    # to the registrable domain, so every host under it receives it,
    # including the platform control-plane host. Kept in SHARED_APPS
    # too, so the public schema retains its own table for platform
    # staff.
    "django.contrib.sessions",
    "user",
    "emails",
    "allauth",
    "allauth.account",
    "allauth.headless",
    "allauth.idp.oidc",
    "allauth.socialaccount",
    "allauth.mfa",
    "allauth.usersessions",
    "allauth.socialaccount.providers.facebook",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.discord",
    "allauth.socialaccount.providers.github",
    "knox",
    # Admin log must be per-schema — mirrored in SHARED_APPS.
    #
    # The merchant admin is served on TENANT hosts (a store OWNER/ADMIN
    # manages their catalogue there), so every admin add/change/delete
    # writes a ``django_admin_log`` row. With admin SHARED-only that
    # table existed ONLY in public, so those INSERTs fell through the
    # search_path into ``public.django_admin_log`` carrying a
    # ``content_type_id`` resolved against the TENANT's own
    # ``django_content_type`` — a different app set, so a different id
    # space (verified in prod: webside product.product CT id 48 is
    # notification.notificationuser in public). Result: mislabelled
    # admin history today, and a hard FK IntegrityError for any tenant
    # whose content-type ids exceed public's range. Dual-listing gives
    # every schema its own log with schema-local content-type ids, the
    # same fix the sibling dual-listed apps above already apply. The
    # public copy stays for the PLATFORM admin (which runs on public).
    # admin's app_label ("admin") is in role_scopes.PLATFORM_ONLY_APP_
    # LABELS and SHARED wins in tenant_only_app_labels(), so this does
    # NOT expose any admin model page to a merchant.
    "admin.apps.MyAdminConfig",
    # Domain apps (store-specific data)
    "product",
    # Reads product/order rows, writes only its own tables; after
    # ``product`` because its models FK into it.
    "recommendation",
    "order",
    "cart",
    "blog",
    "search",
    "notification",
    "contact",
    "loyalty",
    "promotion",
    "giftcard",
    "b2b",
    "page_config",
    # Store-edited storefront texts (overrides of the storefront's locale
    # defaults, validated through the storefront before they are saved)
    "texts",
    # The storefront page builder: versioned documents, releases and the
    # staff API (the block catalogue is the storefront's, ``builder.storefront``)
    "builder",
    # Agent surface (OIDC-bearer API over tenant-schema data; no models
    # of its own — placed here because everything it serves is per-store)
    "agent",
    "shipping",
    "shipping_boxnow",
    "shipping_acs",
    "meta_capi",
    # Tells search engines through IndexNow which product and blog pages
    # changed (``docs/indexnow.md``)
    "indexnow",
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
    # ``django_celery_results`` is deliberately NOT here — task results
    # are public-schema only. See the SHARED_APPS entry for why.
    # Per-tenant setting overrides — mirrored in SHARED_APPS.
    "extra_settings",
    "simple_history",
    "djstripe",
    "meili",
]

INSTALLED_APPS = list(SHARED_APPS) + [
    app for app in TENANT_APPS if app not in SHARED_APPS
]

MIDDLEWARE = [
    # Probe bypass MUST precede TenantMainMiddleware — kubelet probes
    # carry the pod IP as Host, which no TenantDomain row can match.
    "core.middleware.health_probe.HealthProbeMiddleware",
    # Correlation id, then the access line that reports it. Both sit
    # OUTSIDE tenant resolution so a request that fails there (unknown
    # host, suspended store) still gets an id and a log line, and below
    # the probe bypass so kubelet's readiness probe is never logged.
    # The access log must stay inside the correlation id: that layer's
    # ContextVar is reset on the way out.
    "core.middleware.correlation_id.CorrelationIdMiddleware",
    "core.middleware.access_log.AccessLogMiddleware",
    # Internal-token-gated domains feed for cluster-internal services
    # dialing backend-service directly (no resolvable tenant Host) —
    # must answer BEFORE tenant resolution.
    "tenant.internal.InternalDomainsMiddleware",
    # django-tenants' host routing, plus the staff connect host.
    "tenant.middleware.PlatformTenantMainMiddleware",
    # Immediately after tenant resolution and before anything that
    # serves data: django_tenants resolves a host without looking at
    # ``is_active``, so a SUSPENDED store kept answering its API in full.
    "tenant.middleware.SuspendedTenantMiddleware",
    # Response phase runs in reverse: placed here, it rewrites the
    # session/CSRF cookie Domain AFTER Session/Csrf middleware set them
    # — the static *_COOKIE_DOMAIN settings only fit the platform apex.
    "tenant.middleware.TenantCookieDomainMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "core.middleware.locale.RequestLanguageMiddleware",
    "core.middleware.admin_locale.AdminDefaultGreekMiddleware",
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
    # Schema-correct stand-in for allauth.usersessions.middleware
    # .UserSessionsMiddleware — platform-staff session rows go to the
    # public schema (their user row lives there, not in the tenant's
    # user table).
    "tenant.middleware.TenantAwareUserSessionsMiddleware",
    "djangorestframework_camel_case.middleware.CamelCaseMiddleWare",
    "simple_history.middleware.HistoryRequestMiddleware",
    "core.middleware.asgi_compat.ASGICompatMiddleware",  # ASGI compatibility for Rosetta
]

ROOT_URLCONF = "core.urls"

# security.W003 string-matches ``django.middleware.csrf.CsrfViewMiddleware``
# and cannot see that ``tenant.middleware.TenantCsrfMiddleware`` subclasses
# it, so the check is a false positive here.
SILENCED_SYSTEM_CHECKS = ["security.W003"]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Project templates live in core/templates ONLY. The repo-root
        # ``templates/`` directory belongs to python-semantic-release
        # (``[tool.semantic_release.changelog] template_dir``), which
        # renders EVERY file it finds there into the repo root on each
        # release. A Django template parked in it was therefore copied
        # out as a stray top-level directory on every version bump —
        # that is where the tracked ``emails/`` and ``unfold/`` folders
        # at the repo root came from. Keeping it out of DIRS removes the
        # incentive to put Django templates there at all.
        "DIRS": [
            path.join(SRC_DIR, "core/templates"),
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

SITE_ID = int(getenv("SITE_ID", "1"))

ENABLE_DEBUG_TOOLBAR = getenv("ENABLE_DEBUG_TOOLBAR", "False") == "True"

if DEBUG:
    MIDDLEWARE += [
        "core.middleware.stripe_webhook.StripeWebhookDebugMiddleware",
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
        # Request panel surfaces the resolved view + GET/POST/cookies.
        "debug_toolbar.panels.request.RequestPanel",
        # SQL panel is the load-bearing diagnostic for N+1 hunting.
        # Without it the toolbar can show wall-clock time but not
        # WHERE that time goes — which is the only useful question.
        "debug_toolbar.panels.sql.SQLPanel",
        "debug_toolbar.panels.staticfiles.StaticFilesPanel",
        "debug_toolbar.panels.templates.TemplatesPanel",
        # Cache panel reveals get/set/hit/miss on Redis — critical for
        # validating that the admin dashboard's cached queries
        # (``admin:dashboard:*``) are actually warm under load.
        "debug_toolbar.panels.cache.CachePanel",
        "debug_toolbar.panels.signals.SignalsPanel",
        "debug_toolbar.panels.redirects.RedirectsPanel",
        "debug_toolbar.panels.profiling.ProfilingPanel",
    ]
    DEBUG_TOOLBAR_CONFIG = {
        "INTERCEPT_REDIRECTS": False,
        "RESULTS_CACHE_SIZE": 100,
    }
