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

from core.api.views import health_check, redirect_to_frontend
from core.views import (
    HomeView,
    ManageTOTPSvgView,
    csp_report,
    robots_txt,
    upload_image,
)

app_name = "core"

urlpatterns = [
    path("robots.txt", robots_txt, name="robots-txt"),
    path("csp_report/", csp_report, name="csp-report"),
    path("i18n/", include("django.conf.urls.i18n")),
]

urlpatterns += i18n_patterns(
    path("", HomeView.as_view(), name="home"),
    path(_("admin/"), admin.site.urls),
    path("upload_image", upload_image, name="upload_image"),
    path("accounts/", include("allauth.urls")),
    path(
        "account/provider/callback",
        redirect_to_frontend,
        name="provider-callback",
    ),
    path("_allauth/", include("allauth.headless.urls")),
    path(
        "_allauth/app/v1/account/authenticators/totp/svg",
        ManageTOTPSvgView.as_api_view(client="app"),
        name="manage_totp_svg",
    ),
    path("rosetta/", include("rosetta.urls")),
    path("tinymce/", include("tinymce.urls")),
    path("api/v1/", include("product.urls")),
    path("api/v1/", include("order.urls")),
    path("api/v1/", include("user.urls")),
    path("api/v1/", include("country.urls")),
    path("api/v1/", include("region.urls")),
    path("api/v1/", include("search.urls")),
    path("api/v1/", include("blog.urls")),
    path("api/v1/", include("pay_way.urls")),
    path("api/v1/", include("cart.urls")),
    path("api/v1/", include("notification.urls")),
    path("api/v1/", include("contact.urls")),
    path("api/v1/health", health_check, name="api-health"),
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
    prefix_default_language=False,
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
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )

    urlpatterns += staticfiles_urlpatterns()
