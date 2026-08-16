from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers


class AgentProfileSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text=_("User ID"))
    email = serializers.EmailField(help_text=_("Account email"))
    first_name = serializers.CharField(
        allow_blank=True, help_text=_("First name")
    )
    last_name = serializers.CharField(
        allow_blank=True, help_text=_("Last name")
    )
