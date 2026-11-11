from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from emails.models import UNKNOWN_EMAIL_TYPE
from emails.preferences import offered_types

DUPLICATE_EMAIL_TYPE = "duplicate_email_type"


class EmailPreferenceSerializer(serializers.Serializer):
    key = serializers.CharField(read_only=True)
    title = serializers.CharField(read_only=True)
    enabled = serializers.BooleanField(read_only=True)


class EmailPreferencesSerializer(serializers.Serializer):
    types = EmailPreferenceSerializer(many=True, read_only=True)


class EmailPreferenceChoiceSerializer(serializers.Serializer):
    key = serializers.CharField()
    enabled = serializers.BooleanField()


class EmailPreferencesWriteSerializer(serializers.Serializer):
    types = EmailPreferenceChoiceSerializer(many=True, allow_empty=False)

    def validate_types(self, value):
        offered = {email_type.key for email_type in offered_types()}
        keys = [choice["key"] for choice in value]
        if unknown := [key for key in keys if key not in offered]:
            raise serializers.ValidationError(
                _("Not an email type you can choose: %(keys)s.")
                % {"keys": ", ".join(unknown)},
                code=UNKNOWN_EMAIL_TYPE,
            )
        if len(set(keys)) != len(keys):
            raise serializers.ValidationError(
                _("Each email type may appear once."),
                code=DUPLICATE_EMAIL_TYPE,
            )
        return value
