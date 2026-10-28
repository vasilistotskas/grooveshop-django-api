"""The public read of a store's text overrides.

The storefront merges what this returns over its own defaults (a
separate release). It is the store's data only: the language asked for,
the texts the store replaced, and never a locked one.
"""

from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.decorators import (
    api_view,
    permission_classes,
    renderer_classes,
)
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from tenant.membership import bound_tenant
from texts import storefront
from texts.models import TextOverride, TextSurface

LANGUAGE_PARAM = "language"


class TextsUnavailable(APIException):
    """The catalogue that says which texts are locked is not reachable.

    Failing closed: serving an override of a text the platform has since
    locked is worse than serving the defaults for a moment.
    """

    status_code = 503
    default_detail = "The texts are not available right now."
    default_code = "texts_unavailable"


def overrides_cache_key(generation: int, catalogue_hash: str, language: str):
    return f"texts:overrides:{generation}:{catalogue_hash}:{language}"


def _collect(
    catalogue: storefront.Catalogue, language: str
) -> dict[str, dict[str, str]]:
    grouped: dict[str, dict[str, str]] = {}
    rows = TextOverride.objects.filter(
        surface=TextSurface.STOREFRONT, language=language
    ).values_list("namespace", "key", "value")
    for namespace, key, value in rows:
        entry = catalogue.get(namespace, key)
        if entry is None or entry.locked:
            continue
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
                "{namespace: {key: text}}. Locked texts are never included; "
                "keys are the storefront's own ids and are not camelCased."
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
    try:
        catalogue = storefront.fetch_catalogue(connection.schema_name)
    except storefront.StorefrontUnavailable as exc:
        raise TextsUnavailable from exc
    if language not in catalogue.locales:
        raise ValidationError(
            {LANGUAGE_PARAM: ["Unknown language."]}, code="invalid_language"
        )

    tenant = bound_tenant()
    if tenant is None:
        return Response(_collect(catalogue, language))
    key = overrides_cache_key(tenant.cache_generation, catalogue.hash, language)
    data = cache.get(key)
    if data is None:
        data = _collect(catalogue, language)
        cache.set(key, data, settings.TEXTS_OVERRIDES_CACHE_SECONDS)
    return Response(data)
