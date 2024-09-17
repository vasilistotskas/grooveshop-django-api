from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include
from django.urls import path
from django.urls import re_path
from django.utils.translation import gettext_lazy as _
from drf_spectacular.views import SpectacularAPIView
from drf_spectacular.views import SpectacularRedocView
from drf_spectacular.views import SpectacularSwaggerView

from core.api.views import health_check
from core.api.views import redirect_to_frontend
from core.views import HomeView
from core.views import ManageTOTPSvgView
from core.views import upload_image


User = get_user_model()

app_name = "core"


urlpatterns = i18n_patterns(
    path("", HomeView.as_view(), name="home"),
    path(_("admin/"), admin.site.urls),
    path("upload_image", upload_image, name="upload_image"),
    path("accounts/", include("allauth.urls")),
    path("account/provider/callback", redirect_to_frontend, name="provider-callback"),
    path("_allauth/", include("allauth.headless.urls")),
    path(
        "_allauth/app/v1/account/authenticators/totp/svg",
        ManageTOTPSvgView.as_api_view(client="app"),
        name="manage_totp_svg",
    ),
    # rosetta
    path("rosetta/", include("rosetta.urls")),
    # admin html editor
    path("tinymce/", include("tinymce.urls")),
    # api
    path("api/v1/", include("product.urls")),
    path("api/v1/", include("order.urls")),
    path("api/v1/", include("user.urls")),
    path("api/v1/", include("country.urls")),
    path("api/v1/", include("region.urls")),
    path("api/v1/", include("slider.urls")),
    path("api/v1/", include("search.urls")),
    path("api/v1/", include("tip.urls")),
    path("api/v1/", include("blog.urls")),
    path("api/v1/", include("vat.urls")),
    path("api/v1/", include("pay_way.urls")),
    path("api/v1/", include("session.urls")),
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
            settings.py should already have warned the user about it."
        )
    else:
        urlpatterns += [re_path(r"^__debug__/", include(debug_toolbar.urls))]  # type: ignore

if bool(settings.DEBUG) or settings.SYSTEM_ENV in ["dev", "ci"]:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )

    urlpatterns += staticfiles_urlpatterns()
