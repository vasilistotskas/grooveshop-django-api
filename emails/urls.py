from django.urls import path

from emails.views import EmailUnsubscribeView

urlpatterns = [
    path(
        "emails/unsubscribe/<str:token>",
        EmailUnsubscribeView.as_view(),
        name="email-unsubscribe",
    ),
]
