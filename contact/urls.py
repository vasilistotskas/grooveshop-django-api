from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns

from contact.views import ContactCreateView

urlpatterns = [
    path("contact", ContactCreateView.as_view(), name="contact"),
]

urlpatterns = format_suffix_patterns(urlpatterns)
