from django.urls import path
from django.urls import re_path
from django.views.generic import TemplateView

from core.auth.views.registration import AuthConfirmEmailView
from core.auth.views.registration import AuthRegisterView
from core.auth.views.registration import AuthResendEmailVerificationView
from core.auth.views.registration import AuthVerifyEmailView

urlpatterns = [
    path("", AuthRegisterView.as_view(), name="rest_register"),
    path("verify-email/", AuthVerifyEmailView.as_view(), name="rest_verify_email"),
    path(
        "resend-email/",
        AuthResendEmailVerificationView.as_view(),
        name="rest_resend_email",
    ),
    # This url is used by django-allauth and empty TemplateView is
    # defined just to allow reverse() call inside app, for example when email
    # with verification link is being sent, then it's required to render email
    # content.
    # account_confirm_email - You should override this view to handle it in
    # your API client somehow and then, send post to /verify-email/ endpoint
    # with proper key.
    # If you don't want to use API on that step, then just use ConfirmEmailView
    # view from:
    # django-allauth https://github.com/pennersr/django-allauth/blob/master/allauth/account/views.py
    re_path(
        r"^account-confirm-email/(?P<key>[-:\w]+)/$",
        AuthConfirmEmailView.as_view(),
        name="account_confirm_email",
    ),
    path(
        "account-email-verification-sent/",
        TemplateView.as_view(),
        name="account_email_verification_sent",
    ),
]
