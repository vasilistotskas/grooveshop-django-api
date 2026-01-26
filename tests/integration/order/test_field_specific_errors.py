import pytest
from django.core.exceptions import ValidationError
from order.services import OrderService


@pytest.mark.django_db
class TestFieldSpecificErrors:
    """
    For any validation failure during order creation, the error response
    SHALL include field-specific error messages indicating which fields failed validation.
    """

    @pytest.mark.parametrize(
        "missing_field,field_name",
        [
            ("first_name", "first_name"),
            ("last_name", "last_name"),
            ("email", "email"),
            ("street", "street"),
            ("street_number", "street_number"),
            ("city", "city"),
            ("zipcode", "zipcode"),
            ("country_id", "country_id"),
            ("phone", "phone"),
        ],
    )
    def test_missing_address_field_error_is_field_specific(
        self, missing_field, field_name
    ):
        """
        Test that missing address fields produce field-specific error messages.
        """
        # Create valid address
        address = {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "street": "Main St",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": "GR",
            "phone": "+306912345678",
        }

        # Remove the field being tested
        del address[missing_field]

        # Attempt validation
        with pytest.raises(ValidationError) as exc_info:
            OrderService.validate_shipping_address(address)

        # Verify field-specific error
        error_dict = exc_info.value.message_dict
        assert field_name in error_dict, (
            f"Expected field '{field_name}' in error dict, got: {error_dict}"
        )
        assert len(error_dict[field_name]) > 0, (
            f"Expected error message for field '{field_name}'"
        )

    @pytest.mark.parametrize(
        "invalid_email",
        [
            "not-an-email",
            "missing@domain",
            "@nodomain.com",
            "spaces in@email.com",
            "double@@domain.com",
        ],
    )
    def test_invalid_email_error_is_field_specific(self, invalid_email):
        """
        Test that invalid email format produces field-specific error.
        """
        address = {
            "first_name": "John",
            "last_name": "Doe",
            "email": invalid_email,
            "street": "Main St",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": "GR",
            "phone": "+306912345678",
        }

        with pytest.raises(ValidationError) as exc_info:
            OrderService.validate_shipping_address(address)

        error_dict = exc_info.value.message_dict
        assert "email" in error_dict, (
            f"Expected 'email' field in error dict, got: {error_dict}"
        )

    @pytest.mark.parametrize(
        "validation_scenario,expected_fields",
        [
            # Multiple missing fields
            (
                {
                    "first_name": "John",
                    # Missing last_name, email, street, etc.
                    "city": "Athens",
                },
                [
                    "last_name",
                    "email",
                    "street",
                    "street_number",
                    "zipcode",
                    "country_id",
                    "phone",
                ],
            ),
            # Invalid email + missing fields
            (
                {
                    "first_name": "John",
                    "email": "invalid-email",
                    # Missing other fields
                },
                [
                    "email",
                    "last_name",
                    "street",
                    "street_number",
                    "city",
                    "zipcode",
                    "country_id",
                    "phone",
                ],
            ),
        ],
    )
    def test_multiple_validation_errors_are_all_field_specific(
        self, validation_scenario, expected_fields
    ):
        """
        Test that multiple validation failures produce field-specific errors for all fields.
        """
        with pytest.raises(ValidationError) as exc_info:
            OrderService.validate_shipping_address(validation_scenario)

        error_dict = exc_info.value.message_dict

        # Verify all expected fields have errors
        for field in expected_fields:
            assert field in error_dict, (
                f"Expected field '{field}' in error dict, got: {error_dict}"
            )

    def test_validation_error_messages_are_descriptive(self):
        """
        Test that validation error messages are descriptive and helpful.
        """
        address = {
            "first_name": "John",
            # Missing all other required fields
        }

        with pytest.raises(ValidationError) as exc_info:
            OrderService.validate_shipping_address(address)

        error_dict = exc_info.value.message_dict

        # Check that error messages are not empty and contain useful information
        for field, messages in error_dict.items():
            assert len(messages) > 0, f"Field '{field}' has no error messages"
            for message in messages:
                assert len(message) > 0, (
                    f"Field '{field}' has empty error message"
                )
                # Error messages should mention the field or requirement
                assert any(
                    keyword in message.lower()
                    for keyword in ["required", "missing", "invalid", "field"]
                ), f"Error message for '{field}' not descriptive: {message}"
