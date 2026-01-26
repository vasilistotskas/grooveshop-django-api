import pytest
from django.core.exceptions import ValidationError

from order.services import OrderService


class TestPropertyAddressValidation:
    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "missing_field,address_data",
        [
            # Test each required field missing individually
            (
                "first_name",
                {
                    "last_name": "Doe",
                    "email": "john@example.com",
                    "street": "Main St",
                    "street_number": "123",
                    "city": "Athens",
                    "zipcode": "12345",
                    "country_id": 1,
                    "phone": "+30123456789",
                },
            ),
            (
                "last_name",
                {
                    "first_name": "John",
                    "email": "john@example.com",
                    "street": "Main St",
                    "street_number": "123",
                    "city": "Athens",
                    "zipcode": "12345",
                    "country_id": 1,
                    "phone": "+30123456789",
                },
            ),
            (
                "email",
                {
                    "first_name": "John",
                    "last_name": "Doe",
                    "street": "Main St",
                    "street_number": "123",
                    "city": "Athens",
                    "zipcode": "12345",
                    "country_id": 1,
                    "phone": "+30123456789",
                },
            ),
            (
                "street",
                {
                    "first_name": "John",
                    "last_name": "Doe",
                    "email": "john@example.com",
                    "street_number": "123",
                    "city": "Athens",
                    "zipcode": "12345",
                    "country_id": 1,
                    "phone": "+30123456789",
                },
            ),
            (
                "street_number",
                {
                    "first_name": "John",
                    "last_name": "Doe",
                    "email": "john@example.com",
                    "street": "Main St",
                    "city": "Athens",
                    "zipcode": "12345",
                    "country_id": 1,
                    "phone": "+30123456789",
                },
            ),
            (
                "city",
                {
                    "first_name": "John",
                    "last_name": "Doe",
                    "email": "john@example.com",
                    "street": "Main St",
                    "street_number": "123",
                    "zipcode": "12345",
                    "country_id": 1,
                    "phone": "+30123456789",
                },
            ),
            (
                "zipcode",
                {
                    "first_name": "John",
                    "last_name": "Doe",
                    "email": "john@example.com",
                    "street": "Main St",
                    "street_number": "123",
                    "city": "Athens",
                    "country_id": 1,
                    "phone": "+30123456789",
                },
            ),
            (
                "country_id",
                {
                    "first_name": "John",
                    "last_name": "Doe",
                    "email": "john@example.com",
                    "street": "Main St",
                    "street_number": "123",
                    "city": "Athens",
                    "zipcode": "12345",
                    "phone": "+30123456789",
                },
            ),
            (
                "phone",
                {
                    "first_name": "John",
                    "last_name": "Doe",
                    "email": "john@example.com",
                    "street": "Main St",
                    "street_number": "123",
                    "city": "Athens",
                    "zipcode": "12345",
                    "country_id": 1,
                },
            ),
        ],
    )
    def test_missing_required_field_raises_validation_error(
        self, missing_field, address_data
    ):
        """
        Test that missing any required field raises ValidationError.

        For any order creation, the shipping address SHALL contain all required fields.
        """
        with pytest.raises(ValidationError) as exc_info:
            OrderService.validate_shipping_address(address_data)

        # Verify the specific field is in the error dict
        assert missing_field in exc_info.value.message_dict
        assert (
            "required"
            in str(exc_info.value.message_dict[missing_field][0]).lower()
        )

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "field,empty_value",
        [
            ("first_name", ""),
            ("first_name", None),
            ("last_name", ""),
            ("last_name", None),
            ("email", ""),
            ("email", None),
            ("street", ""),
            ("street", None),
            ("street_number", ""),
            ("street_number", None),
            ("city", ""),
            ("city", None),
            ("zipcode", ""),
            ("zipcode", None),
            ("country_id", ""),
            ("country_id", None),
            ("phone", ""),
            ("phone", None),
        ],
    )
    def test_empty_required_field_raises_validation_error(
        self, field, empty_value
    ):
        """
        Test that empty values for required fields raise ValidationError.

        Empty strings and None values should be treated as missing.
        """
        address_data = {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "street": "Main St",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": 1,
            "phone": "+30123456789",
        }
        address_data[field] = empty_value

        with pytest.raises(ValidationError) as exc_info:
            OrderService.validate_shipping_address(address_data)

        # Verify the specific field is in the error dict
        assert field in exc_info.value.message_dict

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "invalid_email",
        [
            "notanemail",
            "missing@domain",
            "@nodomain.com",
            "spaces in@email.com",
            "double@@domain.com",
            "no-tld@domain",
            "john..doe@example.com",
            ".john@example.com",
            "john.@example.com",
        ],
    )
    def test_invalid_email_format_raises_validation_error(self, invalid_email):
        """
        Test that invalid email formats raise ValidationError.

        Email format SHALL be validated.
        """
        address_data = {
            "first_name": "John",
            "last_name": "Doe",
            "email": invalid_email,
            "street": "Main St",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": 1,
            "phone": "+30123456789",
        }

        with pytest.raises(ValidationError) as exc_info:
            OrderService.validate_shipping_address(address_data)

        # Verify email field is in the error dict
        assert "email" in exc_info.value.message_dict
        assert "valid" in str(exc_info.value.message_dict["email"][0]).lower()

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "valid_email",
        [
            "john@example.com",
            "john.doe@example.com",
            "john+tag@example.co.uk",
            "john_doe@example-domain.com",
            "123@example.com",
            "a@b.co",
        ],
    )
    def test_valid_email_format_passes_validation(self, valid_email):
        """
        Test that valid email formats pass validation.

        Valid email formats should be accepted.
        """
        address_data = {
            "first_name": "John",
            "last_name": "Doe",
            "email": valid_email,
            "street": "Main St",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": 1,
            "phone": "+30123456789",
        }

        # Should not raise any exception
        OrderService.validate_shipping_address(address_data)

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "invalid_phone",
        [
            "123",  # Too short (< 8 chars)
            "1234567",  # Too short (< 8 chars)
            "123456789012345678901",  # Too long (> 20 chars)
            "a" * 25,  # Way too long
        ],
    )
    def test_invalid_phone_length_raises_validation_error(self, invalid_phone):
        """
        Test that phone numbers with invalid length raise ValidationError.

        Phone numbers must be between 8 and 20 characters.
        """
        address_data = {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "street": "Main St",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": 1,
            "phone": invalid_phone,
        }

        with pytest.raises(ValidationError) as exc_info:
            OrderService.validate_shipping_address(address_data)

        # Verify phone field is in the error dict
        assert "phone" in exc_info.value.message_dict

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "valid_phone",
        [
            "+30123456789",  # International format
            "1234567890",  # 10 digits
            "+1-555-123-4567",  # US format with dashes
            "(555) 123-4567",  # US format with parentheses
            "12345678",  # Minimum length
            "12345678901234567890",  # Maximum length
        ],
    )
    def test_valid_phone_format_passes_validation(self, valid_phone):
        """
        Test that valid phone formats pass validation.

        Valid phone numbers (8-20 chars) should be accepted.
        """
        address_data = {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "street": "Main St",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": 1,
            "phone": valid_phone,
        }

        # Should not raise any exception
        OrderService.validate_shipping_address(address_data)

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "invalid_country_id",
        [
            0,  # Zero
            -1,  # Negative
            -100,  # Large negative
            "X",  # Single character string
            "",  # Empty string
        ],
    )
    def test_invalid_country_id_raises_validation_error(
        self, invalid_country_id
    ):
        """
        Test that invalid country IDs raise ValidationError.

        Country ID must be a positive integer or valid country code.
        """
        address_data = {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "street": "Main St",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": invalid_country_id,
            "phone": "+30123456789",
        }

        with pytest.raises(ValidationError) as exc_info:
            OrderService.validate_shipping_address(address_data)

        # Verify country_id field is in the error dict
        assert "country_id" in exc_info.value.message_dict

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "valid_country_id",
        [
            1,  # Positive integer
            100,  # Large positive integer
            "US",  # Two-letter country code
            "GR",  # Two-letter country code
            "GB",  # Two-letter country code
        ],
    )
    def test_valid_country_id_passes_validation(self, valid_country_id):
        """
        Test that valid country IDs pass validation.

        Valid country IDs (positive integers or 2-letter codes) should be accepted.
        """
        address_data = {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "street": "Main St",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": valid_country_id,
            "phone": "+30123456789",
        }

        # Should not raise any exception
        OrderService.validate_shipping_address(address_data)

    @pytest.mark.django_db
    def test_complete_valid_address_passes_validation(self):
        """
        Test that a complete valid address passes all validation.

        A complete address with all required fields should pass validation.
        """
        address_data = {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john.doe@example.com",
            "street": "Main Street",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": 1,
            "phone": "+30123456789",
        }

        # Should not raise any exception
        OrderService.validate_shipping_address(address_data)

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "missing_fields,address_data",
        [
            # Multiple missing fields
            (
                ["first_name", "last_name"],
                {
                    "email": "john@example.com",
                    "street": "Main St",
                    "street_number": "123",
                    "city": "Athens",
                    "zipcode": "12345",
                    "country_id": 1,
                    "phone": "+30123456789",
                },
            ),
            (
                ["email", "phone"],
                {
                    "first_name": "John",
                    "last_name": "Doe",
                    "street": "Main St",
                    "street_number": "123",
                    "city": "Athens",
                    "zipcode": "12345",
                    "country_id": 1,
                },
            ),
            (
                ["street", "street_number", "city"],
                {
                    "first_name": "John",
                    "last_name": "Doe",
                    "email": "john@example.com",
                    "zipcode": "12345",
                    "country_id": 1,
                    "phone": "+30123456789",
                },
            ),
            # All fields missing
            (
                [
                    "first_name",
                    "last_name",
                    "email",
                    "street",
                    "street_number",
                    "city",
                    "zipcode",
                    "country_id",
                    "phone",
                ],
                {},
            ),
        ],
    )
    def test_multiple_missing_fields_raise_validation_error(
        self, missing_fields, address_data
    ):
        """
        Test that multiple missing fields are all reported in ValidationError.

        All missing required fields should be reported in a single validation error.
        """
        with pytest.raises(ValidationError) as exc_info:
            OrderService.validate_shipping_address(address_data)

        # Verify all missing fields are in the error dict
        for field in missing_fields:
            assert field in exc_info.value.message_dict

    @pytest.mark.django_db
    def test_multiple_validation_errors_reported_together(self):
        """
        Test that multiple validation errors are reported together.

        Multiple validation errors should be reported in a single ValidationError
        with field-specific messages.
        """
        address_data = {
            "first_name": "John",
            "last_name": "Doe",
            "email": "invalid-email",  # Invalid email
            "street": "Main St",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": -1,  # Invalid country_id
            "phone": "123",  # Invalid phone (too short)
        }

        with pytest.raises(ValidationError) as exc_info:
            OrderService.validate_shipping_address(address_data)

        # Verify all invalid fields are in the error dict
        assert "email" in exc_info.value.message_dict
        assert "country_id" in exc_info.value.message_dict
        assert "phone" in exc_info.value.message_dict

    @pytest.mark.django_db
    def test_validation_error_has_field_specific_messages(self):
        """
        Test that ValidationError contains field-specific error messages.

        Validation errors SHALL include field-specific messages.
        """
        address_data = {
            "first_name": "",  # Empty
            "last_name": "Doe",
            "email": "invalid",  # Invalid format
            "street": "Main St",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": 1,
            "phone": "+30123456789",
        }

        with pytest.raises(ValidationError) as exc_info:
            OrderService.validate_shipping_address(address_data)

        # Verify error dict structure
        error_dict = exc_info.value.message_dict
        assert isinstance(error_dict, dict)

        # Verify field-specific messages
        assert "first_name" in error_dict
        assert isinstance(error_dict["first_name"], list)
        assert len(error_dict["first_name"]) > 0

        assert "email" in error_dict
        assert isinstance(error_dict["email"], list)
        assert len(error_dict["email"]) > 0
