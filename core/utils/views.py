"""
Utility view helpers.

cache_methods
-------------
A class decorator that applies Django's ``cache_page`` to named methods.
``cache_page`` alone is not safe for authenticated APIs because it caches
by URL only: two users with different auth tokens hitting the same URL get
each other's responses.  ``vary_on_headers("Authorization", "Cookie")`` is
chained so Django includes those headers in the cache key, ensuring per-user
(and per-session) segregation.

In test mode or when ``settings.DISABLE_CACHE`` is True the decorator is
a no-op so tests are never affected by cache residue.
"""

import sys

from django.conf import settings
from django.http import QueryDict
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.views.decorators.vary import vary_on_headers
from rest_framework.request import Request


class TranslationsProcessingMixin:
    EXPECTED_TRANSLATION_KEY_PARTS = 3

    @staticmethod
    def process_translations_data(request: Request):
        if request.content_type.startswith("multipart/form-data") and any(
            key.startswith("translations.") for key in request.data
        ):
            data = QueryDict(mutable=True)
            data.update(request.data)

            translations = {}
            for key, value in data.items():
                if key.startswith("translations."):
                    parts = key.split(".")
                    if (
                        len(parts)
                        == TranslationsProcessingMixin.EXPECTED_TRANSLATION_KEY_PARTS
                    ):
                        lang_field, field_name = parts[1], parts[2]
                        translations.setdefault(lang_field, {})[field_name] = (
                            value
                        )

            for key in list(data.keys()):
                if key.startswith("translations."):
                    data.pop(key)

            data["translations"] = translations

            request._full_data = data

        return request


cache_methods_registry = []


def cache_methods(timeout, methods, *, cache=None):
    def class_decorator(cls):
        if cls not in cache_methods_registry:
            cache_methods_registry.append(cls)

        skip_caching = (
            "test" in sys.argv
            or "pytest" in sys.argv[0]
            or any("pytest" in arg for arg in sys.argv)
            or getattr(settings, "DISABLE_CACHE", False)
        )

        if skip_caching:
            return cls

        for method_name in methods:
            func = getattr(cls, method_name)
            class_name = cls.__name__
            key_prefix = f"{class_name}_{method_name}"
            cache_decorator = cache_page(
                timeout, cache=cache, key_prefix=key_prefix
            )
            vary_decorator = vary_on_headers("Authorization", "Cookie")
            # vary_on_headers must wrap cache_page so the Vary header is set
            # before the cache layer reads it for key derivation.
            decorated_func = method_decorator(vary_decorator)(
                method_decorator(cache_decorator)(func)
            )
            setattr(cls, method_name, decorated_func)
        return cls

    return class_decorator
