from unittest.mock import patch

import pytest
from django.test import TestCase
from rest_framework.exceptions import ValidationError

from contact.models import Contact
from contact.serializers import ContactWriteSerializer


class TestContactWriteSerializer(TestCase):
    def setUp(self):
        self.valid_data = {
            "name": "John Doe",
            "email": "john@example.com",
            "message": "This is a valid message with sufficient length.",
        }

    def test_valid_serializer_data(self):
        serializer = ContactWriteSerializer(data=self.valid_data)
        assert serializer.is_valid()

        contact = serializer.save()
        assert contact.name == "John Doe"
        assert contact.email == "john@example.com"
        assert (
            contact.message == "This is a valid message with sufficient length."
        )

    def test_serializer_fields(self):
        serializer = ContactWriteSerializer()

        expected_fields = {
            "id",
            "name",
            "email",
            "message",
            "created_at",
            "updated_at",
            "uuid",
        }
        assert set(serializer.fields.keys()) == expected_fields

        read_only_fields = {"created_at", "updated_at", "uuid"}
        for field_name in read_only_fields:
            assert serializer.fields[field_name].read_only

    def test_validate_name_valid(self):
        serializer = ContactWriteSerializer()

        valid_names = ["Jo", "John", "John Doe", "Mary Jane Smith"]
        for name in valid_names:
            result = serializer.validate_name(name)
            assert result == name.strip()

    def test_validate_name_too_short(self):
        serializer = ContactWriteSerializer()

        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_name("J")

        assert "at least 2 characters" in str(exc_info.value)

    def test_validate_name_empty(self):
        serializer = ContactWriteSerializer()

        with pytest.raises(ValidationError):
            serializer.validate_name("")

    def test_validate_name_whitespace_only(self):
        serializer = ContactWriteSerializer()

        with pytest.raises(ValidationError):
            serializer.validate_name("   ")

    def test_validate_name_strips_whitespace(self):
        serializer = ContactWriteSerializer()

        result = serializer.validate_name("  John Doe  ")
        assert result == "John Doe"

    def test_validate_message_valid(self):
        serializer = ContactWriteSerializer()

        valid_message = "This is a valid message with sufficient length."
        result = serializer.validate_message(valid_message)
        assert result == valid_message.strip()

    def test_validate_message_too_short(self):
        serializer = ContactWriteSerializer()

        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_message("Short")

        assert "at least 10 characters" in str(exc_info.value)

    def test_validate_message_too_long(self):
        serializer = ContactWriteSerializer()

        long_message = "a" * 5001
        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_message(long_message)

        assert "too long" in str(exc_info.value)
        assert "5000 characters" in str(exc_info.value)

    def test_validate_message_empty(self):
        serializer = ContactWriteSerializer()

        with pytest.raises(ValidationError):
            serializer.validate_message("")

    def test_validate_message_whitespace_only(self):
        serializer = ContactWriteSerializer()

        with pytest.raises(ValidationError):
            serializer.validate_message("   \n\n\t   ")

    def test_validate_message_strips_whitespace(self):
        serializer = ContactWriteSerializer()

        message = "  This is a valid message.  "
        result = serializer.validate_message(message)
        assert result == "This is a valid message."

    def test_validate_message_exactly_minimum_length(self):
        serializer = ContactWriteSerializer()

        message = "1234567890"
        result = serializer.validate_message(message)
        assert result == message

    def test_validate_message_exactly_maximum_length(self):
        serializer = ContactWriteSerializer()

        message = "a" * 5000
        result = serializer.validate_message(message)
        assert result == message

    @patch("contact.serializers.validate_contact_content")
    @patch("contact.serializers.sanitize_message")
    def test_validate_method_calls_utils(self, mock_sanitize, mock_validate):
        mock_validate.return_value = {
            "valid": True,
            "errors": {},
            "warnings": [],
        }
        mock_sanitize.return_value = "sanitized message"

        serializer = ContactWriteSerializer(data=self.valid_data)
        assert serializer.is_valid()

        mock_validate.assert_called_once_with(
            "John Doe",
            "john@example.com",
            "This is a valid message with sufficient length.",
        )
        mock_sanitize.assert_called_once_with(
            "This is a valid message with sufficient length."
        )

    @patch("contact.serializers.validate_contact_content")
    def test_validate_method_handles_validation_errors(self, mock_validate):
        mock_validate.return_value = {
            "valid": False,
            "errors": {"name": "Name is invalid", "email": "Email is invalid"},
            "warnings": [],
        }

        serializer = ContactWriteSerializer(data=self.valid_data)
        assert not serializer.is_valid()

        errors = serializer.errors
        assert "non_field_errors" in errors
        error_message = str(errors["non_field_errors"][0])
        assert "name: Name is invalid" in error_message
        assert "email: Email is invalid" in error_message

    @patch("contact.serializers.validate_contact_content")
    def test_validate_method_handles_single_error(self, mock_validate):
        mock_validate.return_value = {
            "valid": False,
            "errors": {"spam": "Message appears to be spam"},
            "warnings": [],
        }

        serializer = ContactWriteSerializer(data=self.valid_data)
        assert not serializer.is_valid()

        error_message = str(serializer.errors["non_field_errors"][0])
        assert "spam: Message appears to be spam" in error_message

    @patch("contact.serializers.validate_contact_content")
    def test_validate_method_handles_no_errors(self, mock_validate):
        mock_validate.return_value = {
            "valid": False,
            "errors": {},
            "warnings": [],
        }

        serializer = ContactWriteSerializer(data=self.valid_data)
        assert serializer.is_valid()

    @patch("contact.serializers.sanitize_message")
    def test_validate_method_sanitizes_message(self, mock_sanitize):
        mock_sanitize.return_value = "cleaned message"

        data = self.valid_data.copy()
        data["message"] = "dirty message with <script>alert('xss')</script>"

        serializer = ContactWriteSerializer(data=data)
        is_valid = serializer.is_valid()

        if is_valid:
            assert serializer.validated_data["message"] == "cleaned message"
            mock_sanitize.assert_called_once_with(
                "dirty message with <script>alert('xss')</script>"
            )

    def test_serializer_with_missing_fields(self):
        incomplete_data = {"name": "John"}

        serializer = ContactWriteSerializer(data=incomplete_data)
        assert not serializer.is_valid()

        assert "email" in serializer.errors
        assert "message" in serializer.errors

    def test_serializer_with_extra_fields(self):
        data_with_extra = self.valid_data.copy()
        data_with_extra["extra_field"] = "should be ignored"

        serializer = ContactWriteSerializer(data=data_with_extra)
        assert serializer.is_valid()

        assert "extra_field" not in serializer.validated_data

    def test_serializer_update_existing_contact(self):
        contact = Contact.objects.create(
            name="Initial Name",
            email="initial@example.com",
            message="Initial message content.",
        )

        update_data = {
            "name": "Updated Name",
            "email": "updated@example.com",
            "message": "Updated message content with sufficient length.",
        }

        serializer = ContactWriteSerializer(instance=contact, data=update_data)
        assert serializer.is_valid()

        updated_contact = serializer.save()
        assert updated_contact.name == "Updated Name"
        assert updated_contact.email == "updated@example.com"
        assert (
            updated_contact.message
            == "Updated message content with sufficient length."
        )

    def test_serializer_representation(self):
        contact = Contact.objects.create(**self.valid_data)

        serializer = ContactWriteSerializer(instance=contact)
        data = serializer.data

        assert data["name"] == "John Doe"
        assert data["email"] == "john@example.com"
        assert (
            data["message"] == "This is a valid message with sufficient length."
        )
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data
        assert "uuid" in data

    def test_field_validation_order(self):
        data = {
            "name": "J",
            "email": "john@example.com",
            "message": "This is a valid message with sufficient length.",
        }

        serializer = ContactWriteSerializer(data=data)
        assert not serializer.is_valid()

        assert "name" in serializer.errors
        assert "at least 2 characters" in str(serializer.errors["name"][0])

    def test_empty_data_validation(self):
        serializer = ContactWriteSerializer(data={})
        assert not serializer.is_valid()

        assert "name" in serializer.errors
        assert "email" in serializer.errors
        assert "message" in serializer.errors
