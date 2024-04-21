import sys

from django.conf import settings
from django.views.decorators.cache import cache_page
from rest_framework.request import Request


class TranslationsProcessingMixin:
    def process_translations_data(self, request: Request) -> Request:
        if request.content_type.startswith("multipart/form-data"):
            # Ensure request.data is mutable by copying the QueryDict
            data = request.data.copy()
            translations = {}

            for key, value in data.items():
                if key.startswith("translations."):
                    parts = key.split(".")
                    if len(parts) == 3:
                        lang_field, field_name = parts[1], parts[2]
                        translations.setdefault(lang_field, {})[field_name] = value

            for key in list(data.keys()):
                if key.startswith("translations."):
                    data.pop(key)

            data["translations"] = translations

            request._full_data = data

        return request


def conditional_cache_page(*args, **kwargs):
    if "test" in sys.argv or settings.DEBUG or settings.CACHE_DISABLED:

        def decorator(func):
            return func

    else:

        def decorator(func):
            return cache_page(*args, **kwargs)(func)

    return decorator
