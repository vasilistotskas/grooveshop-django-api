from django.urls import path
from django.views.generic import TemplateView

urlpatterns = [
    path("signup", TemplateView.as_view(template_name="signup.html"), name="signup"),
    path(
        "email-verification",
        TemplateView.as_view(template_name="email_verification.html"),
        name="email-verification",
    ),
    path("login", TemplateView.as_view(template_name="login.html"), name="login"),
    path("logout", TemplateView.as_view(template_name="logout.html"), name="logout"),
    path(
        "password-reset",
        TemplateView.as_view(template_name="password_reset.html"),
        name="password-reset",
    ),
    path(
        "password-reset/confirm",
        TemplateView.as_view(template_name="password_reset_confirm.html"),
        name="password-reset-confirm",
    ),
    path(
        "user-details",
        TemplateView.as_view(template_name="user_details.html"),
        name="user-details",
    ),
    path(
        "password-change",
        TemplateView.as_view(template_name="password_change.html"),
        name="password-change",
    ),
    path(
        "resend-email-verification",
        TemplateView.as_view(template_name="resend_email_verification.html"),
        name="resend-email-verification",
    ),
    path(
        "password/reset/confirm/<str:uidb64>/<str:token>",
        TemplateView.as_view(template_name="password_reset_confirm.html"),
        name="password_reset_confirm",
    ),
]
