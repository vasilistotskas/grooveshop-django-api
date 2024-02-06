from django.urls import path
from django.urls import re_path

from authentication.views.registration import AuthConfirmEmailView
from authentication.views.registration import AuthEmailVerificationSentView
from authentication.views.registration import AuthRegisterView
from authentication.views.registration import AuthResendEmailVerificationView
from authentication.views.registration import AuthVerifyEmailView

urlpatterns = [
    path("registration", AuthRegisterView.as_view(), name="rest_register"),
    path(
        "registration/verify-email",
        AuthVerifyEmailView.as_view(),
        name="rest_verify_email",
    ),
    path(
        "registration/resend-email",
        AuthResendEmailVerificationView.as_view(),
        name="rest_resend_email",
    ),
    re_path(
        r"^registration/account-confirm-email/(?P<key>[-:\w]+)/$",
        AuthConfirmEmailView.as_view(),
        name="account_confirm_email",
    ),
    path(
        "registration/account-email-verification-sent",
        AuthEmailVerificationSentView.as_view(),
        name="account_email_verification_sent",
    ),
]
