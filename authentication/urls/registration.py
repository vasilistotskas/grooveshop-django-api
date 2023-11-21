from django.urls import path
from django.urls import re_path

from authentication.views.registration import AuthConfirmEmailView
from authentication.views.registration import AuthEmailVerificationSentView
from authentication.views.registration import AuthRegisterView
from authentication.views.registration import AuthResendEmailVerificationView
from authentication.views.registration import AuthVerifyEmailView

urlpatterns = [
    path("", AuthRegisterView.as_view(), name="rest_register"),
    path("verify-email", AuthVerifyEmailView.as_view(), name="rest_verify_email"),
    path(
        "resend-email",
        AuthResendEmailVerificationView.as_view(),
        name="rest_resend_email",
    ),
    re_path(
        r"^account-confirm-email/(?P<key>[-:\w]+)/$",
        AuthConfirmEmailView.as_view(),
        name="account_confirm_email",
    ),
    path(
        "account-email-verification-sent",
        AuthEmailVerificationSentView.as_view(),
        name="account_email_verification_sent",
    ),
]
