from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from contact.views import ContactCreateView, FeedbackCreateView

urlpatterns = [
    path("contact", ContactCreateView.as_view(), name="contact"),
    path("feedback", FeedbackCreateView.as_view(), name="feedback"),
]

urlpatterns = format_suffix_patterns(urlpatterns)
