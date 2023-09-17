from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.http import HttpResponse
from django.urls import include
from django.urls import path
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET
from django_otp.admin import OTPAdminSite
from django_otp.plugins.otp_totp.admin import TOTPDeviceAdmin
from django_otp.plugins.otp_totp.models import TOTPDevice
from drf_spectacular.views import SpectacularAPIView
from drf_spectacular.views import SpectacularRedocView
from drf_spectacular.views import SpectacularSwaggerView
from rest_framework import routers

from core.view import HomeView
from notification.consumers import NotificationConsumer
from user.views.account import ObtainAuthTokenView

app_name = "app"


@require_GET
def robots_txt(request):
    lines = [
        "User-Agent: *",
        "Disallow: /private/",
        "Disallow: /junk/",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


front_urls = [
    path("robots.txt", robots_txt),
]

router = routers.SimpleRouter()


class OTPAdmin(OTPAdminSite):
    pass


admin_site_otp = OTPAdmin(name="OTPAdmin")
admin_site_otp.register(User)
admin_site_otp.register(TOTPDevice, TOTPDeviceAdmin)

urlpatterns = i18n_patterns(
    path("__reload__/", include("django_browser_reload.urls")),
    path("", HomeView.as_view(), name="home"),
    path("", include(front_urls)),
    path("auth/", include("core.auth.urls.auth")),
    path(_("admin/"), admin_site_otp.urls),
    path(_("admin_no_otp/"), admin.site.urls),
    path("accounts/", include("allauth_2fa.urls")),
    path("accounts/", include("allauth.urls")),
    # rosetta
    path("rosetta/", include("rosetta.urls")),
    # admin html editor
    path("tinymce/", include("tinymce.urls")),
    # api
    path("api/v1/api-token-auth/", ObtainAuthTokenView.as_view()),
    path("api/v1/auth/", include("core.auth.urls.base")),
    path("api/v1/auth/registration/", include("core.auth.urls.registration")),
    path("api/v1/auth/", include("core.auth.urls.social")),
    path("api/v1/", include(router.urls)),
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
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/v1/schema/swagger-ui/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/v1/schema/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
    prefix_default_language=False,
)

websocket_urlpatterns = [path("ws/notifications/", NotificationConsumer.as_asgi())]

urlpatterns += static(
    settings.MEDIA_URL,
    document_root=settings.MEDIA_ROOT,
)
urlpatterns += staticfiles_urlpatterns()
