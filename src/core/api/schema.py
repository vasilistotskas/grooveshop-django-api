from collections.abc import Mapping, Sequence
from typing import Any

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.openapi import AutoSchema as SpectacularAutoSchema


class AutoSchema(SpectacularAutoSchema):
    """Custom AutoSchema that respects @action-level pagination_class overrides.

    drf-spectacular's default AutoSchema always uses the viewset-level
    pagination_class for schema generation, even when a custom @action
    explicitly sets ``pagination_class=None``.  DRF only applies action-level
    kwargs at runtime, so the schema generator never sees them.

    This subclass reads the ``pagination_class`` kwarg stored on the action
    function by DRF's ``@action`` decorator and honours it during schema
    generation.

    Convention: custom ``@action`` methods that return plain (unpaginated)
    arrays **must** declare ``pagination_class=None`` in the decorator.
    """

    def _get_paginator(self):
        action = getattr(self.view, "action", None)
        if action:
            action_func = getattr(self.view, action, None)
            if action_func:
                action_kwargs = getattr(action_func, "kwargs", {})
                if "pagination_class" in action_kwargs:
                    pagination_class = action_kwargs["pagination_class"]
                    if pagination_class is None:
                        return None
                    return pagination_class()
        return super()._get_paginator()


def generate_schema_multi_lang(
    model_instance,
    fields: Sequence[str] | None = None,
    max_lengths: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """The ``translations`` object: per language, every translated field,
    or only ``fields`` when a serializer emits a subset.

    ``max_lengths`` publishes ``maxLength`` for a field: pass it for a
    serializer that WRITES the field, never one that reads it, since a
    bound on a response schema rejects text stored before the bound."""
    translated_fields = (
        list(fields)
        if fields is not None
        else model_instance._parler_meta.get_all_fields()
    )
    languages = settings.PARLER_LANGUAGES[settings.SITE_ID]
    bounds = max_lengths or {}

    if not translated_fields or not languages:
        return {"type": "object", "properties": {}}

    # The per-language properties are built as their own dict rather than
    # reached through `fields[code]["properties"][name]`. That chain
    # indexes back into a heterogeneous literal — `{"type": str,
    # "properties": dict}` — whose value type is `str | dict`, so every
    # reader (and every type checker) has to prove which one it got.
    fields: dict[str, Any] = {
        language["code"]: {
            "type": "object",
            "properties": {
                translated_field: {
                    "type": "string",
                    **(
                        {"maxLength": bounds[translated_field]}
                        if translated_field in bounds
                        else {}
                    ),
                }
                for translated_field in translated_fields
            },
        }
        for language in languages
    }
    return {"type": "object", "properties": fields}


def postprocess_schema_parameters_to_accept_strings(
    result, generator, **kwargs
):
    """
    Postprocessing hook to make all parameters accept string representations in addition
    to their original types. This is useful for frontend frameworks that send all query
    parameters as strings.
    """

    def process_parameters(parameters):
        """Process a list of parameters to modify all types to also accept strings."""
        if not parameters:
            return parameters

        for parameter in parameters:
            if "schema" in parameter and "type" in parameter["schema"]:
                param_type = parameter["schema"]["type"]

                if param_type == "integer":
                    parameter["schema"] = {
                        "oneOf": [
                            {"type": "string", "pattern": r"^-?\d+$"},
                            {"type": "integer"},
                        ]
                    }
                elif param_type == "boolean":
                    parameter["schema"] = {
                        "oneOf": [
                            {
                                "type": "string",
                                "enum": ["true", "false", "1", "0"],
                            },
                            {"type": "boolean"},
                        ]
                    }
                elif param_type == "number":
                    parameter["schema"] = {
                        "oneOf": [
                            {"type": "string", "pattern": r"^-?\d+(\.\d+)?$"},
                            {"type": "number"},
                        ]
                    }
                elif param_type == "array":
                    original_array_schema = parameter["schema"].copy()
                    parameter["schema"] = {
                        "oneOf": [
                            {
                                "type": "string",
                                "description": _("Comma-separated values"),
                            },
                            original_array_schema,
                        ]
                    }
        return parameters

    def process_schema_recursive(schema_dict):
        """Recursively process the schema to find and modify parameters."""
        if isinstance(schema_dict, dict):
            if "parameters" in schema_dict:
                schema_dict["parameters"] = process_parameters(
                    schema_dict["parameters"]
                )

            for value in schema_dict.values():
                if isinstance(value, (dict, list)):
                    process_schema_recursive(value)
        elif isinstance(schema_dict, list):
            for item in schema_dict:
                if isinstance(item, (dict, list)):
                    process_schema_recursive(item)

    process_schema_recursive(result)
    return result


#: The error bodies the handler produces (``core.api.exception_handler``).
#: ``publish_error_schemas`` adds them as components and points every
#: plain 400 at ``ApiValidationError`` or its plain bodies.
ERROR_SCHEMAS: dict[str, dict[str, Any]] = {
    "ApiFieldError": {
        "type": "object",
        "description": (
            "One message under a field and its stable code. Show "
            "``message``; branch on ``code``."
        ),
        "properties": {
            "message": {"type": "string"},
            "code": {"type": "string"},
        },
        "required": ["message", "code"],
    },
    "ApiFieldErrors": {
        "description": (
            "What lies under one field: its messages, or, for a nested "
            "serializer, an object (a list serializer keys its rows by "
            "position) or list of the same."
        ),
        "oneOf": [
            {
                "type": "array",
                "items": {
                    "oneOf": [
                        {"$ref": "#/components/schemas/ApiFieldError"},
                        {"$ref": "#/components/schemas/ApiFieldErrors"},
                    ]
                },
            },
            {
                "type": "object",
                "additionalProperties": {
                    "$ref": "#/components/schemas/ApiFieldErrors"
                },
            },
        ],
    },
    "ApiError": {
        "type": "object",
        "description": (
            "A plain API error. ``code`` is stable; ``detail`` is in "
            "the request's language."
        ),
        "properties": {
            "detail": {"type": "string"},
            "code": {"type": "string"},
        },
        "required": ["detail", "code"],
    },
    "ApiValidationError": {
        "type": "object",
        "description": (
            "A validation error: the field keys DRF always sent, plus "
            "``code`` (the one code all errors share, else ``invalid``; "
            "when a field is itself named ``code``, that field's messages "
            "instead) and ``fields``, each field's messages with their "
            "codes. A field of a nested serializer holds the nested object."
        ),
        "properties": {
            "code": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            },
            "fields": {
                "type": "object",
                "additionalProperties": {
                    "$ref": "#/components/schemas/ApiFieldErrors"
                },
            },
        },
        "additionalProperties": True,
        "required": ["fields"],
    },
}


_SCHEMA_REF = "#/components/schemas/"

#: The generic error body the views declare (``create_schema_view_config``).
#: Its ``code`` is optional; the handler always sets it, so where this body
#: is documented beside others it becomes ``ApiError``.
GENERIC_ERROR_BODY = "ErrorResponse"


def _any_of(names: tuple[str, ...]) -> dict[str, Any]:
    return {"anyOf": [{"$ref": f"{_SCHEMA_REF}{name}"} for name in names]}


def _declared_body(operation: dict[str, Any], status: str):
    """Each JSON media of ``status`` whose schema is one ``$ref``, with the
    component it names."""
    response = operation.get("responses", {}).get(status)
    for media in (response or {}).get("content", {}).values():
        ref = media.get("schema", {}).get("$ref", "")
        if ref.startswith(_SCHEMA_REF):
            yield media, ref.removeprefix(_SCHEMA_REF)


def _document_raised_errors(result: dict[str, Any]) -> None:
    """Document beside each declared 400 and 404 body what the handler
    sends for an error any view can raise.

    - A 400 is also a validation error (field keys, ``code``, ``fields``,
      no ``detail``: a serializer's ``is_valid(raise_exception=True)``) and
      a plain one (``ParseError``, ``{"detail", "code"}``). So every 400 is
      ``ApiValidationError`` or ``ApiError`` or the view's own body, if it
      names one (``B2BErrorResponse``, ``GiftCardErrorResponse``).
    - A 404 is also a plain ``NotFound`` (``get_object``, a feature gate):
      a view that names a 404 body of its own documents ``ApiError`` beside
      it.

    Rewriting here, once, keeps the schema equal to
    ``core.api.exception_handler`` for every operation.
    """
    for operations in result.get("paths", {}).values():
        for operation in operations.values():
            if not isinstance(operation, dict):
                continue
            for media, name in _declared_body(operation, "400"):
                own = () if name == GENERIC_ERROR_BODY else (name,)
                media["schema"] = _any_of(
                    ("ApiValidationError", "ApiError", *own)
                )
            for media, name in _declared_body(operation, "404"):
                if name != GENERIC_ERROR_BODY:
                    media["schema"] = _any_of(("ApiError", name))


def publish_error_schemas(
    result: dict[str, Any], generator: Any, request: Any, public: bool
) -> dict[str, Any]:
    schemas = result.setdefault("components", {}).setdefault("schemas", {})
    schemas.update(ERROR_SCHEMAS)
    _document_raised_errors(result)
    return result
