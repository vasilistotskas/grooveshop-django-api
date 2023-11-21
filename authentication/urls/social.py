from django.urls import path

from authentication.views.base import AuthSocialAccountDisconnectView
from authentication.views.base import AuthSocialAccountListView
from authentication.views.social import FacebookConnect
from authentication.views.social import FacebookLogin
from authentication.views.social import GoogleConnect
from authentication.views.social import GoogleLogin

urlpatterns = [
    path(
        "socialaccounts",
        AuthSocialAccountListView.as_view(),
        name="social_account_list",
    ),
    path(
        "socialaccounts/<int:pk>/disconnect",
        AuthSocialAccountDisconnectView.as_view(),
        name="social_account_disconnect",
    ),
    path("google/login", GoogleLogin.as_view(), name="google_login"),
    path("google/connect", GoogleConnect.as_view(), name="google_connect"),
    path("facebook/login", FacebookLogin.as_view(), name="facebook_login"),
    path("facebook/connect", FacebookConnect.as_view(), name="facebook_connect"),
]
