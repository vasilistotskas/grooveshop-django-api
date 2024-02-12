from django.urls import path

from core.view import SettingsListCreateView
from core.view import SettingsRetrieveUpdateDestroyView

urlpatterns = [
    path("settings/", SettingsListCreateView.as_view(), name="settings-list-create"),
    path(
        "settings/<str:key>/",
        SettingsRetrieveUpdateDestroyView.as_view(),
        name="settings-detail",
    ),
]
