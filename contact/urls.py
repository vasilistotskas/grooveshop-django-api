from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from contact.views import (
    ContactAttachmentUploadView,
    ContactCreateView,
    FeedbackCreateView,
)

urlpatterns = [
    path("contact", ContactCreateView.as_view(), name="contact"),
    path(
        "contact/attachment",
        ContactAttachmentUploadView.as_view(),
        name="contact-attachment",
    ),
    path("feedback", FeedbackCreateView.as_view(), name="feedback"),
]

urlpatterns = format_suffix_patterns(urlpatterns)
