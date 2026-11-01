from django.urls import path

from emails.views import EmailUnsubscribeAllView, EmailUnsubscribeView

urlpatterns = [
    path(
        "emails/unsubscribe/<str:token>",
        EmailUnsubscribeView.as_view(),
        name="email-unsubscribe",
    ),
    path(
        "emails/unsubscribe/<str:token>/all",
        EmailUnsubscribeAllView.as_view(),
        name="email-unsubscribe-all",
    ),
]
