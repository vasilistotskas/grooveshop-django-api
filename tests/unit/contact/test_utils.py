from unittest.mock import patch

from contact.utils import (
    detect_spam_patterns,
    sanitize_message,
    validate_contact_content,
)


class TestDetectSpamPatterns:
    def test_spam_keywords_detection(self):
        spam_message = "Congratulations! You've won the lottery! Click here for free money!"
        result = detect_spam_patterns(
            spam_message, "test@example.com", "Test User"
        )
        assert result is True

    def test_single_spam_keyword_not_spam(self):
        message = "I need help with my order discount code"
        result = detect_spam_patterns(message, "test@example.com", "Test User")
        assert result is False

    def test_multiple_spam_keywords_triggers_spam(self):
        message = "Best deal ever! Guaranteed lowest price! Act now!"
        result = detect_spam_patterns(message, "test@example.com", "Test User")
        assert result is True

    def test_long_message_spam_detection(self):
        long_message = "a" * 5001
        result = detect_spam_patterns(
            long_message, "test@example.com", "Test User"
        )
        assert result is True

    def test_multiple_links_spam_detection(self):
        message = "Check out http://site1.com and http://site2.com and http://site3.com and http://site4.com"
        result = detect_spam_patterns(message, "test@example.com", "Test User")
        assert result is True

    def test_repeated_characters_spam_detection(self):
        message = "HEEEEEEEEEEELLLLLLLLLLPPPPPPPPP MEEEEEEEEEE"
        result = detect_spam_patterns(message, "test@example.com", "Test User")
        assert result is True

    def test_very_short_message_spam_detection(self):
        message = "Hi"
        result = detect_spam_patterns(message, "test@example.com", "Test User")
        assert result is True

    def test_normal_message_not_spam(self):
        message = "Hello, I have a question about your product catalog and pricing options."
        result = detect_spam_patterns(message, "test@example.com", "Test User")
        assert result is False

    def test_case_insensitive_spam_detection(self):
        message = "VIAGRA CASINO LOTTERY winner!"
        result = detect_spam_patterns(message, "test@example.com", "Test User")
        assert result is True

    def test_exactly_three_spam_keywords_triggers_spam(self):
        message = (
            "Special promotion with guaranteed discount - limited time offer!"
        )
        result = detect_spam_patterns(message, "test@example.com", "Test User")
        assert result is True


class TestSanitizeMessage:
    def test_remove_html_tags(self):
        message = "Hello <script>alert('test')</script> world <b>bold</b> text"
        result = sanitize_message(message)
        expected = "Hello alert('test') world bold text"
        assert result == expected

    def test_reduce_repeated_characters(self):
        message = "Helllllllllllo woooooooorld"
        result = sanitize_message(message)
        expected = "Helllo wooorld"
        assert result == expected

    def test_normalize_whitespace(self):
        message = "Hello    world\n\n\nwith   multiple   spaces"
        result = sanitize_message(message)
        expected = "Hello world with multiple spaces"
        assert result == expected

    def test_strip_leading_trailing_whitespace(self):
        message = "   Hello world   "
        result = sanitize_message(message)
        expected = "Hello world"
        assert result == expected

    def test_empty_message_handling(self):
        message = ""
        result = sanitize_message(message)
        expected = ""
        assert result == expected

    def test_whitespace_only_message(self):
        message = "   \n\n\t\t   "
        result = sanitize_message(message)
        expected = ""
        assert result == expected

    def test_complex_sanitization(self):
        message = "  <div>Hellllllllllo</div>    woooooorld\n\n\nwith<script>bad</script>   multiple   spaces  "
        result = sanitize_message(message)
        expected = "Helllo wooorld withbad multiple spaces"
        assert result == expected

    def test_preserve_normal_repeated_characters(self):
        message = "Hello world with some letters like 'book' and 'cool'"
        result = sanitize_message(message)
        expected = "Hello world with some letters like 'book' and 'cool'"
        assert result == expected


class TestValidateContactContent:
    def test_valid_contact_content(self):
        result = validate_contact_content(
            name="John Doe",
            email="john@example.com",
            message="This is a valid message with sufficient length.",
        )
        assert result["valid"] is True
        assert len(result["errors"]) == 0
        assert "warnings" in result

    def test_short_name_validation(self):
        result = validate_contact_content(
            name="J",
            email="john@example.com",
            message="This is a valid message with sufficient length.",
        )
        assert result["valid"] is False
        assert "name" in result["errors"]
        assert "at least 2 characters" in str(result["errors"]["name"])

    def test_empty_name_validation(self):
        result = validate_contact_content(
            name="",
            email="john@example.com",
            message="This is a valid message with sufficient length.",
        )
        assert result["valid"] is False
        assert "name" in result["errors"]

    def test_whitespace_only_name_validation(self):
        result = validate_contact_content(
            name="  ",
            email="john@example.com",
            message="This is a valid message with sufficient length.",
        )
        assert result["valid"] is False
        assert "name" in result["errors"]

    def test_short_message_validation(self):
        result = validate_contact_content(
            name="John Doe", email="john@example.com", message="Short"
        )
        assert result["valid"] is False
        assert "message" in result["errors"]
        assert "at least 10 characters" in str(result["errors"]["message"])

    def test_long_message_validation(self):
        long_message = "a" * 5001
        result = validate_contact_content(
            name="John Doe", email="john@example.com", message=long_message
        )
        assert result["valid"] is False
        assert "message" in result["errors"]
        assert "too long" in str(result["errors"]["message"])

    def test_invalid_email_validation(self):
        result = validate_contact_content(
            name="John Doe",
            email="invalid-email",
            message="This is a valid message with sufficient length.",
        )
        assert result["valid"] is False
        assert "email" in result["errors"]
        assert "valid email" in str(result["errors"]["email"])

    def test_various_invalid_email_formats(self):
        invalid_emails = [
            "test@",
            "@example.com",
            "test@.com",
            "test.example.com",
            "test@example",
            "test@@example.com",
            "",
        ]

        for invalid_email in invalid_emails:
            result = validate_contact_content(
                name="John Doe",
                email=invalid_email,
                message="This is a valid message with sufficient length.",
            )
            assert result["valid"] is False
            assert "email" in result["errors"]

    def test_valid_email_formats(self):
        valid_emails = [
            "test@example.com",
            "user.name@example.com",
            "user+tag@example.co.uk",
            "123@example.org",
            "test-email@sub.example.com",
        ]

        for valid_email in valid_emails:
            result = validate_contact_content(
                name="John Doe",
                email=valid_email,
                message="This is a valid message with sufficient length.",
            )
            assert "email" not in result["errors"] or "valid email" not in str(
                result["errors"].get("email", "")
            )

    @patch("contact.utils.detect_spam_patterns")
    def test_spam_detection_integration(self, mock_detect_spam):
        mock_detect_spam.return_value = True

        result = validate_contact_content(
            name="John Doe",
            email="john@example.com",
            message="This is a valid message with sufficient length.",
        )

        assert result["valid"] is False
        assert "spam" in result["errors"]
        assert "appears to be spam" in str(result["errors"]["spam"])
        mock_detect_spam.assert_called_once()

    @patch("contact.utils.is_disposable_domain")
    def test_disposable_email_detection(self, mock_is_disposable):
        mock_is_disposable.return_value = True

        result = validate_contact_content(
            name="John Doe",
            email="test@tempmail.com",
            message="This is a valid message with sufficient length.",
        )

        assert result["valid"] is False
        assert "email" in result["errors"]
        assert "Disposable" in str(result["errors"]["email"])
        mock_is_disposable.assert_called_once_with("tempmail.com")

    def test_multiple_validation_errors(self):
        result = validate_contact_content(
            name="J", email="invalid-email", message="Short"
        )

        assert result["valid"] is False
        assert len(result["errors"]) >= 3
        assert "name" in result["errors"]
        assert "email" in result["errors"]
        assert "message" in result["errors"]

    def test_whitespace_message_validation(self):
        result = validate_contact_content(
            name="John Doe", email="john@example.com", message="   \n\n\t   "
        )
        assert result["valid"] is False
        assert "message" in result["errors"]

    def test_exactly_minimum_length_inputs(self):
        result = validate_contact_content(
            name="Jo",
            email="a@b.co",
            message="1234567890",
        )
        assert "name" not in result[
            "errors"
        ] or "at least 2 characters" not in str(result["errors"]["name"])
        assert "message" not in result[
            "errors"
        ] or "at least 10 characters" not in str(result["errors"]["message"])
