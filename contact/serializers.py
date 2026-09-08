import re

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from contact.models import Contact, Feedback
from contact.utils import (
    sanitize_message,
    validate_contact_content,
    validate_feedback_content,
)

# Digits plus the punctuation phone numbers are written with, in any
# country: `+30 2310 924 440`, `(0030) 2310-924440 ext. 12` is not
# accepted — an extension goes in the message.
_PHONE_RE = re.compile(r"[0-9+()\-.\s]{5,30}")


class ContactWriteSerializer(serializers.ModelSerializer[Contact]):
    class Meta:
        model = Contact
        fields = (
            "id",
            "name",
            "email",
            "message",
            "company",
            "phone",
            "subject",
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

    def validate_company(self, value: str) -> str:
        return sanitize_message(value)

    def validate_phone(self, value: str) -> str:
        """Digits and the punctuation a phone number is written with.

        Not a format check: an office number, a mobile, an
        international prefix and an extension are all valid here, and
        the platform serves more than one country. This only refuses
        the field being used as a second message body.
        """
        cleaned = sanitize_message(value)
        if cleaned and not _PHONE_RE.fullmatch(cleaned):
            raise serializers.ValidationError(
                _("Enter a phone number, using digits and + ( ) - only.")
            )
        return cleaned

    def validate_subject(self, value: str) -> str:
        return sanitize_message(value)

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


class FeedbackWriteSerializer(serializers.ModelSerializer[Feedback]):
    class Meta:
        model = Feedback
        fields = (
            "id",
            "name",
            "email",
            "rating",
            "category",
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
        rating = attrs.get("rating")

        validation_result = validate_feedback_content(
            name, email, message, rating
        )

        if not validation_result["valid"]:
            errors = validation_result["errors"]

            error_messages = []
            for field, error in errors.items():
                error_messages.append(f"{field}: {error}")

            if error_messages:
                raise serializers.ValidationError(", ".join(error_messages))

        attrs["message"] = sanitize_message(message)
        attrs["name"] = name.strip()

        return attrs

    def validate_rating(self, value: int) -> int:
        if not 1 <= value <= 5:
            raise serializers.ValidationError(
                _("Rating must be between 1 and 5.")
            )
        return value
