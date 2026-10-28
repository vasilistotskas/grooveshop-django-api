"""The public read of a store's text overrides.

The storefront merges what this returns over its own defaults. It reads
the database and the cache and nothing else: every storefront render
asks for it, so it must never call the storefront back (a slow storefront
pod would then fail text delivery for every store).

Locked texts and keys the storefront's build no longer has are the
storefront's to ignore when it merges (``isLockedText`` is its own shared
code). Every override was checked by the storefront when it was saved, so
only a lock added since then can matter, and the storefront knows its own
locks.
"""

from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.decorators import (
    api_view,
    permission_classes,
    renderer_classes,
)
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from core.utils.tenant_urls import STOREFRONT_LOCALES
from tenant.membership import bound_tenant
from texts.models import TextOverride, TextSurface

LANGUAGE_PARAM = "language"


def overrides_cache_key(generation: int, language: str) -> str:
    return f"texts:overrides:{generation}:{language}"


def _collect(language: str) -> dict[str, dict[str, str]]:
    grouped: dict[str, dict[str, str]] = {}
    rows = TextOverride.objects.filter(
        surface=TextSurface.STOREFRONT, language=language
    ).values_list("namespace", "key", "value")
    for namespace, key, value in rows:
        grouped.setdefault(namespace, {})[key] = value
    return grouped


# Keys are the storefront's own ids (``code_label``, ``components/Footer``),
# so the body must not go through the camelCase renderer, which would
# rewrite them.
@extend_schema(  # ty: ignore[invalid-argument-type]
    parameters=[
        OpenApiParameter(
            name=LANGUAGE_PARAM,
            location=OpenApiParameter.QUERY,
            required=True,
            type=str,
            description="A language code the storefront serves (el, en).",
        )
    ],
    responses={
        200: {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            "description": (
                "The store's replacement texts for the language, "
                "{namespace: {key: text}}, as stored: from the database and "
                "the cache only, never by asking the storefront. The "
                "storefront ignores a locked text (isLockedText) and a key "
                "its build no longer has when it merges. Keys are the "
                "storefront's own ids and are not camelCased."
            ),
        }
    },
    tags=["Texts"],
)
@api_view(["GET"])
@permission_classes([AllowAny])
@renderer_classes([JSONRenderer])
def public_text_overrides(request):
    language = (request.query_params.get(LANGUAGE_PARAM) or "").strip()
    if language not in STOREFRONT_LOCALES:
        raise ValidationError(
            {LANGUAGE_PARAM: ["Unknown language."]}, code="invalid_language"
        )

    tenant = bound_tenant()
    if tenant is None:
        return Response(_collect(language))
    key = overrides_cache_key(tenant.cache_generation, language)
    data = cache.get(key)
    if data is None:
        data = _collect(language)
        cache.set(key, data, settings.TEXTS_OVERRIDES_CACHE_SECONDS)
    return Response(data)
