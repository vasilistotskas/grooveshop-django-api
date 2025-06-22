from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from contact.models import Contact
from contact.utils import sanitize_message, validate_contact_content


class ContactWriteSerializer(serializers.ModelSerializer[Contact]):
    class Meta:
        model = Contact
        fields = (
            "id",
            "name",
            "email",
            "message",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
        )

    def validate(self, attrs):
        name = attrs.get("name", "")
        email = attrs.get("email", "")
        message = attrs.get("message", "")

        validation_result = validate_contact_content(name, email, message)

        if not validation_result["valid"]:
            errors = validation_result["errors"]

            error_messages = []
            for field, error in errors.items():
                error_messages.append(f"{field}: {error}")

            if error_messages:
                raise serializers.ValidationError(", ".join(error_messages))

        attrs["message"] = sanitize_message(message)

        return attrs

    def validate_name(self, value: str) -> str:
        if len(value.strip()) < 2:
            raise serializers.ValidationError(
                _("Name must be at least 2 characters long.")
            )
        return value.strip()

    def validate_message(self, value: str) -> str:
        if len(value.strip()) < 10:
            raise serializers.ValidationError(
                _("Message must be at least 10 characters long.")
            )
        if len(value) > 5000:
            raise serializers.ValidationError(
                _("Message is too long. Maximum 5000 characters allowed.")
            )
        return value.strip()
