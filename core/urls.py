from urllib.parse import urlsplit

from allauth.idp.oidc import views as oidc_views
from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path
from django.utils.translation import gettext_lazy as _
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

import core.filters.camel_case_filters
import core.filters.camel_case_ordering  # noqa
from core.api.views import (
    get_setting_by_key,
    health_check,
    health_live,
    list_settings,
)
from core.rosetta_views import DBBackedTranslationFormView
from core.views import (
    HomeView,
    ManageTOTPSvgView,
    robots_txt,
    upload_image,
)
from order.views.viva_webhook import viva_wallet_webhook
from shipping_boxnow.views.webhook import BoxNowWebhookView

app_name = "core"

# ---------------------------------------------------------------------------
# URL surface is split into named groups so the two hosts can compose
# different subsets. django-tenants serves ``PUBLIC_SCHEMA_URLCONF``
# (``tenant.urls_public``) on the PUBLIC schema — i.e. only the platform
# control-plane host (``platform.grooveshop.space``) — and this
# ``ROOT_URLCONF`` on every tenant/storefront host.
#
# ``_storefront_*`` groups carry a store's catalogue, orders, customers,
# cart, loyalty and agent resources. They are mounted on the tenant host
# ONLY. The platform host composes ``public_shared_urlpatterns`` (below),
# which omits them, so the control plane never exposes a merchant's
# storefront API — even though it sits behind the platform's auth wall,
# defence in depth keeps store data structurally absent there rather than
# reachable-but-empty. Everything a tenant host serves is UNCHANGED: its
# ``urlpatterns`` is the shared groups + the storefront groups, in the
# same shape as before.
# ---------------------------------------------------------------------------

# Root-mounted (outside i18n_patterns), served on BOTH hosts: locale
# machinery, payment/shipping webhook receivers (POST-only, no data
# exposure; the sender targets whichever host the webhook was registered
# with, so the receiver stays available on both), and OAuth/OIDC AS
# discovery + headless-auth endpoints.
_root_shared_patterns = [
    path("robots.txt", robots_txt, name="robots-txt"),
    path("i18n/", include("django.conf.urls.i18n")),
    path("stripe/", include("djstripe.urls", namespace="djstripe")),
    path(
        "viva-wallet/webhook/",
        viva_wallet_webhook,
        name="viva-wallet-webhook",
    ),
    path(
        "boxnow/webhook/",
        BoxNowWebhookView.as_view(),
        name="boxnow-webhook",
    ),
    # allauth headless endpoints must be at the root (not inside i18n_patterns)
    # so non-default locales don't get a /{lang}/_allauth/ prefix that
    # the Nuxt proxy never sends.
    path("_allauth/", include("allauth.headless.urls")),
    path(
        "_allauth/app/v1/account/authenticators/totp/svg",
        ManageTOTPSvgView.as_api_view(client="app"),
        name="manage_totp_svg",
    ),
    # OIDC identity provider (allauth.idp): /.well-known/openid-configuration
    # + /identity/o/* (authorize, token, DCR, …). Root-mounted — OAuth
    # clients (AI agents) construct these URLs from the issuer, never with
    # a locale prefix.
    path("", include("allauth.idp.urls")),
    # RFC 8414 alias: plain-OAuth clients look for
    # /.well-known/oauth-authorization-server; serve the same metadata
    # document as the OIDC discovery endpoint.
    path(
        ".well-known/oauth-authorization-server",
        oidc_views.configuration,
        name="oauth_authorization_server_metadata",
    ),
]

# Root-mounted, STOREFRONT only: agent-facing scoped resources (a store's
# orders/loyalty for an authenticated AI agent). Outside i18n_patterns for
# the same reason as the IdP endpoints.
_root_storefront_patterns = [
    path("api/v1/", include("agent.urls")),
]

# Locale-prefixed, served on BOTH hosts: admin + editor infra (the platform
# admin login flow relies on ``accounts/``; unfold/tinymce/rosetta/image
# upload are admin dependencies) and shared reference/control-plane data
# (country/region, tenant resolve + memberships, health, settings, schema).
_shared_i18n_patterns = [
    path("", HomeView.as_view(), name="home"),
    path("upload_image", upload_image, name="upload_image"),
    path("accounts/", include("allauth.urls")),
    # Our DBBackedTranslationFormView overrides the same URL that rosetta.urls
    # registers for the translation form; Django resolves the first match
    # so this override takes priority over the default `TranslationFormView`.
    path(
        "rosetta/files/<str:po_filter>/<str:lang_id>/<int:idx>/",
        DBBackedTranslationFormView.as_view(),
        name="rosetta-form",
    ),
    path("rosetta/", include("rosetta.urls")),
    path("tinymce/", include("tinymce.urls")),
    path("api/v1/", include("country.urls")),
    path("api/v1/", include("region.urls")),
    path("api/v1/", include("tenant.urls")),
    path("api/v1/health", health_check, name="api-health"),
    path("api/v1/health/live", health_live, name="api-health-live"),
    path("api/v1/settings", list_settings, name="api-settings-list"),
    path("api/v1/settings/get", get_setting_by_key, name="api-settings-get"),
    path("api/v1/schema", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/v1/schema/swagger-ui",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/v1/schema/redoc",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
]

# Locale-prefixed, STOREFRONT only: merchant admin tooling that reads
# TENANT_APPS data. Kept in its own group because it MUST be resolved
# before ``admin/`` — ``AdminSite`` ends its URLconf with a catch-all
# (``final_catch_all_view``) that would answer ``admin/email-templates/*``
# with a 404 instead of letting resolution fall through. It is absent
# from ``public_shared_urlpatterns`` on purpose: these views query
# ``Order``, whose table does not exist in the public schema.
_storefront_admin_i18n_patterns = [
    path(
        _("admin/email-templates/"),
        include("core.email.urls", namespace="email_templates"),
    ),
    # The STORE admin, and storefront-only. It used to sit in
    # ``_shared_i18n_patterns``, which put it on the platform host as
    # well — and ``tenant/urls_public.py`` shadows only the UNPREFIXED
    # ``admin/`` with the control-plane site. So on the platform host
    # ``/admin/login/`` reached ``PlatformAdminSite`` (superuser only)
    # while ``/en/admin/login/`` reached ``MyAdminSite``, whose
    # ``has_permission`` admits any ``is_staff`` platform identity.
    # Verified with ``resolve(..., urlconf="tenant.urls_public")``:
    #
    #   /admin/login/     -> site=PlatformAdminSite
    #   /en/admin/login/  -> site=MyAdminSite
    #   /en/admin/clear-cache/ -> MyAdminSite.clear_cache_view
    #
    # A store owner could open the cache page there and purge globally:
    # on the public schema ``_current_tenant_host()`` is None, so the
    # Nuxt purge goes out with no host and flushes every store's SSR
    # cache. The platform host now serves ``platform_admin_site`` and
    # nothing else.
    #
    # After email-templates, not before: ``AdminSite`` ends its URLconf
    # with ``final_catch_all_view``, which would answer
    # ``admin/email-templates/*`` with a 404 instead of letting
    # resolution fall through.
    path(_("admin/"), admin.site.urls),
]

# Locale-prefixed, STOREFRONT only: the merchant storefront/commerce API.
_storefront_i18n_patterns = [
    path("api/v1/", include("product.urls")),
    path("api/v1/", include("order.urls")),
    path("api/v1/", include("user.urls")),
    path("api/v1/", include("search.urls")),
    path("api/v1/", include("blog.urls")),
    path("api/v1/", include("tag.urls")),
    path("api/v1/", include("pay_way.urls")),
    path("api/v1/", include("shipping.urls")),
    path("api/v1/", include("shipping_boxnow.urls")),
    path("api/v1/", include("shipping_acs.urls")),
    path("api/v1/", include("cart.urls")),
    path("api/v1/", include("notification.urls")),
    path("api/v1/", include("contact.urls")),
    path("api/v1/", include("loyalty.urls")),
    path("api/v1/", include("giftcard.urls")),
    path("api/v1/", include("b2b.urls")),
    path("api/v1/", include("page_config.urls")),
    path("api/v1/", include("promotion.urls")),
]

# Platform control-plane host (PUBLIC schema) surface, minus the
# platform-only admin/staff endpoints that ``tenant.urls_public`` prepends.
# Consumed there via ``from core.urls import public_shared_urlpatterns``.
public_shared_urlpatterns = _root_shared_patterns + i18n_patterns(
    *_shared_i18n_patterns,
    prefix_default_language=False,
)

# Tenant/storefront host (ROOT_URLCONF): shared groups + storefront groups.
urlpatterns = (
    _root_shared_patterns
    + _root_storefront_patterns
    + i18n_patterns(
        *_storefront_admin_i18n_patterns,
        *_shared_i18n_patterns,
        *_storefront_i18n_patterns,
        prefix_default_language=False,
    )
)

if bool(settings.ENABLE_DEBUG_TOOLBAR):
    import warnings

    try:
        import debug_toolbar
    except ImportError:
        warnings.warn(
            "The debug toolbar was not installed. Ignore the error. \
            settings.py should already have warned the user about it.",
            stacklevel=2,
        )
    else:
        urlpatterns += [
            path("__debug__/", include(debug_toolbar.urls)),
        ]

if bool(settings.DEBUG) or settings.SYSTEM_ENV in ["dev", "ci"]:
    # ``MEDIA_URL`` is absolute in every environment (so DRF
    # ``ImageField`` serialises to a full URL the Zod 4 schema
    # accepts), but Django's ``static()`` helper no-ops when the
    # prefix has a netloc — it would refuse to route ``/media/<path>``
    # if we passed the full URL. Strip down to just the path portion
    # so the dev media-serve view still mounts.
    _media_path = urlsplit(settings.MEDIA_URL).path or settings.MEDIA_URL
    urlpatterns += static(
        _media_path,
        document_root=settings.MEDIA_ROOT,
    )

    urlpatterns += staticfiles_urlpatterns()
