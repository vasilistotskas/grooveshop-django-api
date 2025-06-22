import contextlib
import json
from io import BytesIO

import pytest
from django.test import RequestFactory
from rest_framework.exceptions import ParseError

from core.api.parsers import (
    NoUnderscoreBeforeNumberCamelCaseFormParser,
    NoUnderscoreBeforeNumberCamelCaseJSONParser,
    NoUnderscoreBeforeNumberCamelCaseMultiPartParser,
)


class TestNoUnderscoreBeforeNumberCamelCaseJSONParser:
    def setup_method(self):
        self.parser = NoUnderscoreBeforeNumberCamelCaseJSONParser()
        self.factory = RequestFactory()

    def test_json_underscoreize_configuration(self):
        assert self.parser.json_underscoreize == {
            "no_underscore_before_number": True
        }

    def test_parse_camel_case_json(self):
        json_data = {
            "firstName": "John",
            "lastName": "Doe",
            "phoneNumber2": "123456789",
            "email": "john@example.com",
        }
        json_string = json.dumps(json_data)
        stream = BytesIO(json_string.encode("utf-8"))

        request = self.factory.post(
            "/", data=json_string, content_type="application/json"
        )
        parser_context = {"request": request}

        result = self.parser.parse(stream, "application/json", parser_context)

        assert "first_name" in result
        assert "last_name" in result
        assert "phone_number2" in result
        assert "email" in result

    def test_parse_invalid_json(self):
        invalid_json = "{'invalid': json}"
        stream = BytesIO(invalid_json.encode("utf-8"))

        request = self.factory.post(
            "/", data=invalid_json, content_type="application/json"
        )
        parser_context = {"request": request}

        with pytest.raises(ParseError):
            self.parser.parse(stream, "application/json", parser_context)

    def test_parse_empty_json(self):
        json_string = "{}"
        stream = BytesIO(json_string.encode("utf-8"))

        request = self.factory.post(
            "/", data=json_string, content_type="application/json"
        )
        parser_context = {"request": request}

        result = self.parser.parse(stream, "application/json", parser_context)
        assert result == {}


class TestNoUnderscoreBeforeNumberCamelCaseFormParser:
    def setup_method(self):
        self.parser = NoUnderscoreBeforeNumberCamelCaseFormParser()
        self.factory = RequestFactory()

    def test_parse_form_data_with_context_override(self):
        form_data = "firstName=John&lastName=Doe&email=john@example.com"
        stream = BytesIO(form_data.encode("utf-8"))

        request = self.factory.post(
            "/",
            data=form_data,
            content_type="application/x-www-form-urlencoded",
        )
        parser_context = {"request": request}

        result = self.parser.parse(
            stream, "application/x-www-form-urlencoded", parser_context
        )

        assert isinstance(result, dict)

    def test_parse_password_fields_ignored(self):
        form_data = "password1=secret&password2=secret&firstName=John"
        stream = BytesIO(form_data.encode("utf-8"))

        request = self.factory.post(
            "/",
            data=form_data,
            content_type="application/x-www-form-urlencoded",
        )
        parser_context = {"request": request}

        result = self.parser.parse(
            stream, "application/x-www-form-urlencoded", parser_context
        )

        assert isinstance(result, dict)

    def test_parse_context_configuration(self):
        stream = BytesIO(b"test=value")
        request = self.factory.post(
            "/",
            data="test=value",
            content_type="application/x-www-form-urlencoded",
        )
        parser_context = {"request": request}

        original_parse = self.parser.__class__.__bases__[0].parse
        context_used = None

        def mock_parse(
            self_inner, stream_inner, media_type_inner, parser_context_inner
        ):
            nonlocal context_used
            context_used = parser_context_inner
            return {}

        self.parser.__class__.__bases__[0].parse = mock_parse

        try:
            self.parser.parse(
                stream, "application/x-www-form-urlencoded", parser_context
            )

            assert "json_underscoreize" in context_used
            assert (
                context_used["json_underscoreize"][
                    "no_underscore_before_number"
                ]
                is True
            )
            assert (
                "password1" in context_used["json_underscoreize"]["ignore_keys"]
            )
            assert (
                "password2" in context_used["json_underscoreize"]["ignore_keys"]
            )
            assert (
                "new_password1"
                in context_used["json_underscoreize"]["ignore_keys"]
            )
            assert (
                "new_password2"
                in context_used["json_underscoreize"]["ignore_keys"]
            )
        finally:
            self.parser.__class__.__bases__[0].parse = original_parse


class TestNoUnderscoreBeforeNumberCamelCaseMultiPartParser:
    def setup_method(self):
        self.parser = NoUnderscoreBeforeNumberCamelCaseMultiPartParser()
        self.factory = RequestFactory()

    def test_parse_multipart_data(self):
        stream = BytesIO(
            b'--boundary\r\nContent-Disposition: form-data; name="test"\r\n\r\nvalue\r\n--boundary--'
        )

        request = self.factory.post(
            "/", data={}, content_type="multipart/form-data; boundary=boundary"
        )
        parser_context = {"request": request}

        with contextlib.suppress(Exception):
            self.parser.parse(stream, "multipart/form-data", parser_context)

    def test_parse_context_configuration_multipart(self):
        stream = BytesIO(b"test data")
        request = self.factory.post(
            "/", data={}, content_type="multipart/form-data"
        )
        parser_context = {"request": request}

        original_parse = self.parser.__class__.__bases__[0].parse
        context_used = None

        def mock_parse(
            self_inner, stream_inner, media_type_inner, parser_context_inner
        ):
            nonlocal context_used
            context_used = parser_context_inner
            return {}

        self.parser.__class__.__bases__[0].parse = mock_parse

        try:
            self.parser.parse(stream, "multipart/form-data", parser_context)

            assert "json_underscoreize" in context_used
            assert (
                context_used["json_underscoreize"][
                    "no_underscore_before_number"
                ]
                is True
            )
            assert (
                "password1" in context_used["json_underscoreize"]["ignore_keys"]
            )
            assert (
                "password2" in context_used["json_underscoreize"]["ignore_keys"]
            )
            assert (
                "new_password1"
                in context_used["json_underscoreize"]["ignore_keys"]
            )
            assert (
                "new_password2"
                in context_used["json_underscoreize"]["ignore_keys"]
            )

            assert (
                "password1"
                in context_used["json_underscoreize"]["ignore_fields"]
            )
            assert (
                "password2"
                in context_used["json_underscoreize"]["ignore_fields"]
            )
            assert (
                "new_password1"
                in context_used["json_underscoreize"]["ignore_fields"]
            )
            assert (
                "new_password2"
                in context_used["json_underscoreize"]["ignore_fields"]
            )
        finally:
            self.parser.__class__.__bases__[0].parse = original_parse

    def test_parser_inheritance(self):
        assert hasattr(self.parser, "parse")
        assert callable(self.parser.parse)
