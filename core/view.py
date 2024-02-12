from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.views import View
from rest_framework import generics
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

from core.models import Settings
from core.serializers import SettingsSerializer


class HomeView(View):
    template_name = "home.html"

    def get(self, request):
        app_title = settings.SITE_NAME
        return render(request, self.template_name, {"app_title": app_title})


class SettingsListCreateView(generics.ListCreateAPIView):
    queryset = Settings.objects.all()
    serializer_class = SettingsSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if self.request.user.is_superuser:
            return Settings.objects.all()
        return Settings.objects.filter(is_public=True)

    def perform_create(self, serializer):
        if not self.request.user.is_superuser and not serializer.validated_data.get(
            "is_public", True
        ):
            raise Http404("Only superusers can create private settings.")
        serializer.save()


class SettingsRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Settings.objects.all()
    serializer_class = SettingsSerializer
    lookup_field = "key"
    permission_classes = [IsAuthenticated]

    def get_object(self):
        obj = super().get_object()
        if not obj.is_public and not self.request.user.is_superuser:
            raise PermissionDenied("You do not have permission to access this setting.")
        return obj

    def perform_update(self, serializer):
        if not self.request.user.is_superuser:
            raise PermissionDenied("Only superusers can update private settings.")
        serializer.save()

    def perform_destroy(self, instance):
        if not self.request.user.is_superuser:
            raise PermissionDenied("Only superusers can delete private settings.")
        instance.delete()
