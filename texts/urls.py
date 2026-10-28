from django.urls import path

from texts.views import public_text_overrides

app_name = "texts"

urlpatterns = [
    path(
        "texts/overrides",
        public_text_overrides,
        name="text-overrides",
    ),
]
