from unittest.mock import patch

import pytest

from contact.utils import validate_feedback_content

pytestmark = pytest.mark.assert_english


class TestValidateFeedbackContent:
    def test_valid_feedback_content(self):
        result = validate_feedback_content(
            name="John Doe",
            email="john@example.com",
            message="This is a valid piece of feedback for the store.",
            rating=5,
        )
        assert result["valid"] is True
        assert len(result["errors"]) == 0
        assert "warnings" in result

    def test_valid_anonymous_feedback_no_name_no_email(self):
        result = validate_feedback_content(
            name="",
            email="",
            message="This is a valid piece of feedback for the store.",
            rating=3,
        )
        assert result["valid"] is True
        assert "name" not in result["errors"]
        assert "email" not in result["errors"]

    def test_short_name_when_provided_is_invalid(self):
        result = validate_feedback_content(
            name="J",
            email="",
            message="This is a valid piece of feedback for the store.",
            rating=4,
        )
        assert result["valid"] is False
        assert "name" in result["errors"]
        assert "at least 2 characters" in str(result["errors"]["name"])

    def test_whitespace_only_name_treated_as_empty(self):
        result = validate_feedback_content(
            name="   ",
            email="",
            message="This is a valid piece of feedback for the store.",
            rating=4,
        )
        assert "name" not in result["errors"]

    def test_short_message_validation(self):
        result = validate_feedback_content(
            name="John Doe", email="", message="Short", rating=3
        )
        assert result["valid"] is False
        assert "message" in result["errors"]
        assert "at least 10 characters" in str(result["errors"]["message"])

    def test_long_message_validation(self):
        long_message = "a" * 5001
        result = validate_feedback_content(
            name="John Doe", email="", message=long_message, rating=3
        )
        assert result["valid"] is False
        assert "message" in result["errors"]
        assert "too long" in str(result["errors"]["message"])

    def test_invalid_email_when_provided(self):
        result = validate_feedback_content(
            name="John Doe",
            email="invalid-email",
            message="This is a valid piece of feedback for the store.",
            rating=3,
        )
        assert result["valid"] is False
        assert "email" in result["errors"]
        assert "valid email" in str(result["errors"]["email"])

    def test_valid_email_when_provided(self):
        result = validate_feedback_content(
            name="John Doe",
            email="john@example.com",
            message="This is a valid piece of feedback for the store.",
            rating=3,
        )
        assert "email" not in result["errors"]

    @patch("contact.utils.is_disposable_domain")
    def test_disposable_email_rejected_when_provided(self, mock_is_disposable):
        mock_is_disposable.return_value = True

        result = validate_feedback_content(
            name="John Doe",
            email="test@tempmail.com",
            message="This is a valid piece of feedback for the store.",
            rating=3,
        )
        assert result["valid"] is False
        assert "email" in result["errors"]
        assert "Disposable" in str(result["errors"]["email"])
        mock_is_disposable.assert_called_once_with("tempmail.com")

    @pytest.mark.parametrize("rating", [0, 6, -1, 100])
    def test_rating_out_of_range(self, rating):
        result = validate_feedback_content(
            name="John Doe",
            email="",
            message="This is a valid piece of feedback for the store.",
            rating=rating,
        )
        assert result["valid"] is False
        assert "rating" in result["errors"]
        assert "between 1 and 5" in str(result["errors"]["rating"])

    @pytest.mark.parametrize("rating", [1, 2, 3, 4, 5])
    def test_rating_in_range_is_valid(self, rating):
        result = validate_feedback_content(
            name="John Doe",
            email="",
            message="This is a valid piece of feedback for the store.",
            rating=rating,
        )
        assert "rating" not in result["errors"]

    @pytest.mark.parametrize("rating", [None, "5", 3.5, [5]])
    def test_rating_wrong_type_is_invalid(self, rating):
        result = validate_feedback_content(
            name="John Doe",
            email="",
            message="This is a valid piece of feedback for the store.",
            rating=rating,
        )
        assert result["valid"] is False
        assert "rating" in result["errors"]

    def test_spam_detection_integration(self):
        result = validate_feedback_content(
            name="John Doe",
            email="",
            message="Best deal ever! Guaranteed lowest price! Act now!",
            rating=3,
        )
        assert result["valid"] is False
        assert "spam" in result["errors"]
        assert "appears to be spam" in str(result["errors"]["spam"])

    def test_multiple_validation_errors(self):
        result = validate_feedback_content(
            name="J", email="invalid-email", message="Short", rating=10
        )
        assert result["valid"] is False
        assert "name" in result["errors"]
        assert "email" in result["errors"]
        assert "message" in result["errors"]
        assert "rating" in result["errors"]
