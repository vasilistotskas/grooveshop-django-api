from unittest.mock import patch

import pytest
from django.test import TestCase
from rest_framework.exceptions import ValidationError

from contact.models import Feedback, FeedbackCategory
from contact.serializers import FeedbackWriteSerializer

pytestmark = pytest.mark.assert_english


class TestFeedbackWriteSerializer(TestCase):
    def setUp(self):
        self.valid_data = {
            "name": "John Doe",
            "email": "john@example.com",
            "rating": 5,
            "category": FeedbackCategory.WEBSITE,
            "message": "This checkout experience was smooth and fast.",
        }

    def test_valid_serializer_data(self):
        serializer = FeedbackWriteSerializer(data=self.valid_data)
        assert serializer.is_valid(), serializer.errors

        feedback = serializer.save()
        assert feedback.name == "John Doe"
        assert feedback.email == "john@example.com"
        assert feedback.rating == 5
        assert feedback.category == FeedbackCategory.WEBSITE

    def test_valid_serializer_without_name_and_email(self):
        data = self.valid_data.copy()
        data.pop("name")
        data.pop("email")

        serializer = FeedbackWriteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

        feedback = serializer.save()
        assert feedback.name == ""
        assert feedback.email == ""

    def test_valid_serializer_with_blank_name_and_email(self):
        data = self.valid_data.copy()
        data["name"] = ""
        data["email"] = ""

        serializer = FeedbackWriteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_serializer_fields(self):
        serializer = FeedbackWriteSerializer()

        expected_fields = {
            "id",
            "name",
            "email",
            "rating",
            "category",
            "message",
            "created_at",
            "updated_at",
            "uuid",
        }
        assert set(serializer.fields.keys()) == expected_fields

        read_only_fields = {"created_at", "updated_at", "uuid"}
        for field_name in read_only_fields:
            assert serializer.fields[field_name].read_only

    def test_rating_field_is_required(self):
        serializer = FeedbackWriteSerializer()
        assert serializer.fields["rating"].required

    def test_rating_out_of_range_rejected(self):
        data = self.valid_data.copy()
        data["rating"] = 6

        serializer = FeedbackWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "rating" in serializer.errors

    def test_rating_below_range_rejected(self):
        data = self.valid_data.copy()
        data["rating"] = 0

        serializer = FeedbackWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "rating" in serializer.errors

    def test_validate_rating_direct_call(self):
        serializer = FeedbackWriteSerializer()

        with pytest.raises(ValidationError) as exc_info:
            serializer.validate_rating(7)

        assert "between 1 and 5" in str(exc_info.value)

    def test_validate_rating_valid_values(self):
        serializer = FeedbackWriteSerializer()
        for rating in range(1, 6):
            assert serializer.validate_rating(rating) == rating

    def test_message_too_short_rejected(self):
        data = self.valid_data.copy()
        data["message"] = "Short"

        serializer = FeedbackWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "non_field_errors" in serializer.errors
        assert "message" in str(serializer.errors["non_field_errors"][0])

    def test_spam_message_rejected(self):
        data = self.valid_data.copy()
        data["message"] = "Best deal ever! Guaranteed lowest price! Act now!"

        serializer = FeedbackWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "non_field_errors" in serializer.errors
        assert "spam" in str(serializer.errors["non_field_errors"][0])

    @patch("contact.utils.is_disposable_domain")
    def test_disposable_email_rejected_when_provided(self, mock_is_disposable):
        mock_is_disposable.return_value = True
        data = self.valid_data.copy()
        data["email"] = "test@tempmail.com"

        serializer = FeedbackWriteSerializer(data=data)
        assert not serializer.is_valid()
        assert "non_field_errors" in serializer.errors
        assert "email" in str(serializer.errors["non_field_errors"][0])

    def test_missing_required_fields(self):
        serializer = FeedbackWriteSerializer(data={})
        assert not serializer.is_valid()

        assert "rating" in serializer.errors
        assert "message" in serializer.errors

    def test_message_is_sanitized_and_name_stripped(self):
        data = self.valid_data.copy()
        data["name"] = "  John Doe  "
        data["message"] = "  This    message   has   extra   spacing.  "

        serializer = FeedbackWriteSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

        assert serializer.validated_data["name"] == "John Doe"
        assert (
            serializer.validated_data["message"]
            == "This message has extra spacing."
        )

    def test_serializer_representation(self):
        feedback = Feedback.objects.create(
            name="John Doe",
            email="john@example.com",
            rating=4,
            category=FeedbackCategory.SUPPORT,
            message="Support answered my question quickly and clearly.",
        )

        serializer = FeedbackWriteSerializer(instance=feedback)
        data = serializer.data

        assert data["name"] == "John Doe"
        assert data["rating"] == 4
        assert data["category"] == FeedbackCategory.SUPPORT
        assert "id" in data
        assert "uuid" in data
