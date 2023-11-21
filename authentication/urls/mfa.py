from django.urls import path

from authentication.views.mfa import ActivateTotpAPIView
from authentication.views.mfa import AuthenticateTotpAPIView
from authentication.views.mfa import DeactivateTotpAPIView
from authentication.views.mfa import GenerateRecoveryCodesAPIView
from authentication.views.mfa import totp_active
from authentication.views.mfa import ViewRecoveryCodesAPIView

urlpatterns = [
    path(
        "mfa/totp/authenticate",
        AuthenticateTotpAPIView.as_view(),
        name="mfa_totp_authenticate",
    ),
    path("mfa/totp/activate", ActivateTotpAPIView.as_view(), name="mfa_totp_activate"),
    path("mfa/totp/active", totp_active, name="mfa_totp_active"),
    path(
        "mfa/totp/deactivate",
        DeactivateTotpAPIView.as_view(),
        name="mfa_totp_deactivate",
    ),
    path(
        "mfa/recovery-codes/generate",
        GenerateRecoveryCodesAPIView.as_view(),
        name="mfa_recovery_codes_generate",
    ),
    path(
        "mfa/recovery-codes/list",
        ViewRecoveryCodesAPIView.as_view(),
        name="mfa_recovery_codes_list",
    ),
]
