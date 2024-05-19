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
from django_otp.admin import OTPAdminSite
from django_otp.plugins.otp_totp.admin import TOTPDeviceAdmin
from django_otp.plugins.otp_totp.models import TOTPDevice
from drf_spectacular.views import SpectacularAPIView
from drf_spectacular.views import SpectacularRedocView
from drf_spectacular.views import SpectacularSwaggerView

from core.view import HomeView
from core.view import upload_image
from user.views.account import ObtainAuthTokenView

User = get_user_model()

app_name = "core"


class OTPAdmin(OTPAdminSite):
    pass


admin_site_otp = OTPAdmin(name="OTPAdmin")
admin_site_otp.register(User)
admin_site_otp.register(TOTPDevice, TOTPDeviceAdmin)

urlpatterns = i18n_patterns(
    path("__reload__/", include("django_browser_reload.urls")),
    path("", HomeView.as_view(), name="home"),
    path(_("admin/"), admin_site_otp.urls),
    path(_("admin_no_otp/"), admin.site.urls),
    path("upload_image", upload_image, name="upload_image"),
    path("accounts/", include("allauth.mfa.urls")),
    path("accounts/", include("allauth.urls")),
    # rosetta
    path("rosetta/", include("rosetta.urls")),
    # admin html editor
    path("tinymce/", include("tinymce.urls")),
    # api
    path("api/v1/auth/", include("authentication.urls.base")),
    path("api/v1/auth/", include("authentication.urls.registration")),
    path("api/v1/auth/", include("authentication.urls.social")),
    path("api/v1/auth/", include("authentication.urls.mfa")),
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
    path("api/v1/api-token-auth", ObtainAuthTokenView.as_view()),
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
        urlpatterns += [
            re_path(r"^__debug__/", include(debug_toolbar.urls))  # type: ignore
        ]

if bool(settings.DEBUG) or settings.SYSTEM_ENV in ["dev", "ci", "docker"]:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )

    urlpatterns += [
        path("auth/", include("authentication.urls.auth")),
    ]

    urlpatterns += staticfiles_urlpatterns()
