from django.urls import path

from core.auth.views.base import AuthSocialAccountDisconnectView
from core.auth.views.base import AuthSocialAccountListView
from core.auth.views.social import FacebookConnect
from core.auth.views.social import FacebookLogin
from core.auth.views.social import GoogleConnect
from core.auth.views.social import GoogleLogin

urlpatterns = [
    path(
        "socialaccounts/",
        AuthSocialAccountListView.as_view(),
        name="social_account_list",
    ),
    path(
        "socialaccounts/<int:pk>/disconnect/",
        AuthSocialAccountDisconnectView.as_view(),
        name="social_account_disconnect",
    ),
    path("google/login/", GoogleLogin.as_view(), name="google_login"),
    path("google/connect/", GoogleConnect.as_view(), name="google_connect"),
    path("facebook/login/", FacebookLogin.as_view(), name="facebook_login"),
    path("facebook/connect/", FacebookConnect.as_view(), name="facebook_connect"),
]
