from djangorestframework_camel_case.parser import CamelCaseFormParser
from djangorestframework_camel_case.parser import CamelCaseJSONParser
from djangorestframework_camel_case.parser import CamelCaseMultiPartParser


class NoUnderscoreBeforeNumberCamelCaseJSONParser(CamelCaseJSONParser):
    json_underscoreize = {
        "no_underscore_before_number": True,
        "ignore_keys": ("password1", "password2", "new_password1", "new_password2"),
        "ignore_fields": ("password1", "password2", "new_password1", "new_password2"),
    }


class NoUnderscoreBeforeNumberCamelCaseFormParser(CamelCaseFormParser):
    def parse(self, stream, media_type=None, parser_context=None):
        return super().parse(
            stream,
            media_type,
            {
                **parser_context,
                "json_underscoreize": {
                    "no_underscore_before_number": True,
                    "ignore_keys": (
                        "password1",
                        "password2",
                        "new_password1",
                        "new_password2",
                    ),
                    "ignore_fields": (
                        "password1",
                        "password2",
                        "new_password1",
                        "new_password2",
                    ),
                },
            },
        )


class NoUnderscoreBeforeNumberCamelCaseMultiPartParser(CamelCaseMultiPartParser):
    def parse(self, stream, media_type=None, parser_context=None):
        return super().parse(
            stream,
            media_type,
            {
                **parser_context,
                "json_underscoreize": {
                    "no_underscore_before_number": True,
                    "ignore_keys": (
                        "password1",
                        "password2",
                        "new_password1",
                        "new_password2",
                    ),
                    "ignore_fields": (
                        "password1",
                        "password2",
                        "new_password1",
                        "new_password2",
                    ),
                },
            },
        )
