import sys

from django.conf import settings
from django.http import QueryDict
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from rest_framework.request import Request


class TranslationsProcessingMixin:
    def process_translations_data(self, request: Request) -> Request:
        if request.content_type.startswith("multipart/form-data") and any(
            key.startswith("translations.") for key in request.data.keys()
        ):
            data = QueryDict(mutable=True)
            data.update(request.data)

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

            # Update the request data
            request._full_data = data

        return request


cache_methods_registry = []


def cache_methods(timeout, methods, *, cache=None):
    def class_decorator(cls):
        if "test" in sys.argv or getattr(settings, "DISABLE_CACHE", False):
            return cls
        for method_name in methods:
            func = getattr(cls, method_name)
            class_name = cls.__name__
            key_prefix = f"{class_name}_{method_name}"
            cache_decorator = cache_page(timeout, cache=cache, key_prefix=key_prefix)
            decorated_func = method_decorator(cache_decorator)(func)
            setattr(cls, method_name, decorated_func)
        if cls not in cache_methods_registry:
            cache_methods_registry.append(cls)
        return cls

    return class_decorator
