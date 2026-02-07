import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict

from django.db import models
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from parler_rest.fields import TranslatedFieldsField
from rest_framework import serializers
from drf_spectacular.utils import OpenApiParameter


class TranslatedFieldExtended(TranslatedFieldsField):
    def to_representation(self, value):
        """Convert null values to empty strings in translations."""
        result = super().to_representation(value)
        if isinstance(result, dict):
            for lang_code, fields in result.items():
                if isinstance(fields, dict):
                    for field_name, field_value in fields.items():
                        if field_value is None:
                            fields[field_name] = ""
        return result

    def to_internal_value(self, data):
        if data is None:
            return {}
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            self.fail("invalid")
        if not self.allow_empty and len(data) == 0:
            self.fail("empty")

        result, errors = {}, {}
        for lang_code, model_fields in data.items():
            serializer = self.serializer_class(data=model_fields)
            if serializer.is_valid():
                result[lang_code] = serializer.validated_data
            else:
                errors[lang_code] = serializer.errors

        if errors:
            raise serializers.ValidationError(errors)
        return result


def camel_to_words(name):
    result = re.sub(r"(?<!^)(?=[A-Z])", " ", name)
    return result


class DisplayConfig(TypedDict):
    tag: NotRequired[str]
    display_name: NotRequired[str]
    display_name_plural: NotRequired[str]


@dataclass(frozen=True, slots=True)
class ActionConfig:
    """Per-action serializer and schema configuration."""

    request: type[serializers.Serializer] | None = None
    response: type[serializers.Serializer] | None = None
    responses: dict[int, type[serializers.Serializer] | None] | None = None
    operation_id: str | None = None
    summary: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    parameters: list | None = None
    deprecated: bool = False
    many: bool = False

    def get_response_map(self, *, error_serializer=None, default_status=200):
        result = {}
        if error_serializer:
            result.update(
                {
                    400: error_serializer,
                    401: error_serializer,
                    403: error_serializer,
                    404: error_serializer,
                    500: error_serializer,
                }
            )
        if self.response is not None:
            result[default_status] = (
                self.response(many=True) if self.many else self.response
            )
        if self.responses:
            result.update(self.responses)
        return result


type SerializersConfig = dict[str, ActionConfig]


def crud_config(
    *,
    list: type[serializers.Serializer],
    detail: type[serializers.Serializer],
    write: type[serializers.Serializer] | None = None,
) -> SerializersConfig:
    """Convenience factory for the most common CRUD pattern."""
    config: SerializersConfig = {
        "list": ActionConfig(response=list),
        "retrieve": ActionConfig(response=detail),
    }
    if write:
        config["create"] = ActionConfig(request=write, response=detail)
        config["update"] = ActionConfig(request=write, response=detail)
        config["partial_update"] = ActionConfig(request=write, response=detail)
    return config


def _resolve_display_names(model_class, display_config):
    """Resolve display_name, display_name_plural, and tag from model and config."""
    model_name = model_class.__name__

    display_name = display_config.get("display_name")
    if display_name is None:
        if hasattr(model_class._meta, "verbose_name"):
            display_name = model_class._meta.verbose_name
        else:
            display_name = camel_to_words(model_name).lower()

    display_name_plural = display_config.get("display_name_plural")
    if display_name_plural is None:
        if hasattr(model_class._meta, "verbose_name_plural"):
            display_name_plural = model_class._meta.verbose_name_plural
        elif isinstance(display_name, Promise):
            display_name_plural = display_name
        else:
            display_name_plural = f"{display_name}s"

    tag = display_config.get("tag")
    if tag is None:
        if hasattr(model_class._meta, "verbose_name_plural"):
            tag = model_class._meta.verbose_name_plural
        else:
            tag = camel_to_words(model_name)

    return display_name, display_name_plural, tag


def _build_language_parameter(include_language_param):
    """Build language OpenAPI parameter if enabled."""
    if not include_language_param:
        return None
    from django.conf import settings

    try:
        available_languages = [
            lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
        ]
        default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
        return OpenApiParameter(
            name="language_code",
            description=_("Language code for translations (%s)")
            % ", ".join(available_languages),
            required=False,
            type=str,
            enum=available_languages,
            default=default_language,
        )
    except (AttributeError, KeyError):
        return None


def _build_pagination_parameters(include_pagination_params):
    """Build pagination OpenAPI parameters if enabled."""
    if not include_pagination_params:
        return []
    return [
        OpenApiParameter(
            name="pagination_type",
            description=_("Pagination strategy type"),
            required=False,
            type=str,
            enum=["pageNumber", "cursor", "limitOffset"],
            default="pageNumber",
        ),
        OpenApiParameter(
            name="pagination",
            description=_("Enable or disable pagination"),
            required=False,
            type=str,
            enum=["true", "false"],
            default="true",
        ),
        OpenApiParameter(
            name="page_size",
            description=_("Number of results to return per page"),
            required=False,
            type=int,
            default=20,
        ),
    ]


# Default status codes per CRUD action
_CRUD_DEFAULT_STATUS = {
    "create": 201,
    "destroy": 204,
}

# Default summaries/descriptions per CRUD action
_CRUD_TEMPLATES = {
    "list": {
        "op_prefix": "list",
        "summary": _("List %(name)s"),
        "description": _(
            "Retrieve a list of %(name)s with filtering and search capabilities. "
            "Supports multiple pagination strategies: pageNumber (default), cursor, and limitOffset."
        ),
        "use_plural": True,
    },
    "create": {
        "op_prefix": "create",
        "summary": _("Create a %(name)s"),
        "description": _("Create a new %(name)s. Requires authentication."),
        "use_plural": False,
    },
    "retrieve": {
        "op_prefix": "retrieve",
        "summary": _("Retrieve a %(name)s"),
        "description": _("Get detailed information about a specific %(name)s."),
        "use_plural": False,
    },
    "update": {
        "op_prefix": "update",
        "summary": _("Update a %(name)s"),
        "description": _(
            "Update %(name)s information. Requires authentication."
        ),
        "use_plural": False,
    },
    "partial_update": {
        "op_prefix": "partialUpdate",
        "summary": _("Partially update a %(name)s"),
        "description": _(
            "Partially update %(name)s information. Requires authentication."
        ),
        "use_plural": False,
    },
    "destroy": {
        "op_prefix": "destroy",
        "summary": _("Delete a %(name)s"),
        "description": _("Delete a %(name)s. Requires authentication."),
        "use_plural": False,
    },
}

# Error codes to filter per action type
_NO_400_ACTIONS = {"retrieve", "destroy"}
_NO_500_ACTIONS = {"destroy"}


def create_schema_view_config(
    model_class: type[models.Model],
    serializers_config: SerializersConfig,
    error_serializer=None,
    additional_responses=None,
    display_config: DisplayConfig | Mapping[str, Any] | None = None,
    include_language_param: bool = True,
    include_pagination_params: bool = True,
):
    """
    Create schema configuration for a ViewSet with translation support.

    Iterates over ALL keys in ``serializers_config`` – including custom
    actions – and auto-generates ``@extend_schema`` for each.
    """
    if additional_responses is None:
        additional_responses = {}
    if display_config is None:
        display_config = {}

    model_name = model_class.__name__
    display_name, display_name_plural, tag = _resolve_display_names(
        model_class, display_config
    )

    language_parameter = _build_language_parameter(include_language_param)
    pagination_parameters = _build_pagination_parameters(
        include_pagination_params
    )

    list_parameters = []
    if language_parameter:
        list_parameters.append(language_parameter)
    list_parameters.extend(pagination_parameters)

    config = {}

    for action_name, ac in serializers_config.items():
        default_status = _CRUD_DEFAULT_STATUS.get(action_name, 200)
        tpl = _CRUD_TEMPLATES.get(action_name)

        # ── operation_id ──
        if ac.operation_id:
            operation_id = ac.operation_id
        elif tpl:
            operation_id = f"{tpl['op_prefix']}{model_name}"
        else:
            operation_id = f"{action_name}{model_name}"

        # ── summary / description ──
        name_for_tpl = (
            display_name_plural
            if (tpl and tpl.get("use_plural"))
            else display_name
        )
        if ac.summary:
            summary = ac.summary
        elif tpl:
            summary = tpl["summary"] % {"name": name_for_tpl}
        else:
            summary = f"{action_name.replace('_', ' ').title()} {display_name}"

        if ac.description:
            description = ac.description
        elif tpl:
            description = tpl["description"] % {"name": name_for_tpl}
        else:
            description = summary

        # ── tags ──
        tags = ac.tags if ac.tags else [tag]

        # ── parameters ──
        if ac.parameters is not None:
            parameters = ac.parameters
        elif action_name == "list":
            parameters = list_parameters
        elif action_name in {"create", "retrieve", "update", "partial_update"}:
            parameters = [language_parameter] if language_parameter else None
        else:
            parameters = None

        # ── request ──
        request_body = ac.request

        # ── responses ──
        responses = ac.get_response_map(
            error_serializer=error_serializer,
            default_status=default_status,
        )

        # Filter inappropriate error codes
        if action_name in _NO_400_ACTIONS:
            responses.pop(400, None)
        if action_name in _NO_500_ACTIONS:
            responses.pop(500, None)

        # Apply additional_responses overrides (backward compat)
        if action_name in additional_responses:
            responses.update(additional_responses[action_name])

        # Handle list action many=True
        if action_name == "list" and default_status in responses:
            resp_val = responses[default_status]
            if isinstance(resp_val, type) and issubclass(
                resp_val, serializers.Serializer
            ):
                responses[default_status] = resp_val(many=True)

        config[action_name] = extend_schema(
            operation_id=operation_id,
            summary=summary,
            description=description,
            tags=tags,
            parameters=parameters,
            request=request_body,
            responses=responses,
            deprecated=ac.deprecated if ac.deprecated else None,
        )

    # Ensure CRUD actions that have no config still get a schema entry
    for crud_action in [
        "list",
        "create",
        "retrieve",
        "update",
        "partial_update",
        "destroy",
    ]:
        if crud_action not in config:
            tpl = _CRUD_TEMPLATES[crud_action]
            crud_default_status = _CRUD_DEFAULT_STATUS.get(crud_action, 200)
            name_for_tpl = (
                display_name_plural if tpl.get("use_plural") else display_name
            )

            responses = {}
            if error_serializer:
                responses = {
                    400: error_serializer,
                    401: error_serializer,
                    403: error_serializer,
                    404: error_serializer,
                    500: error_serializer,
                }
            if crud_action in _NO_400_ACTIONS:
                responses.pop(400, None)
            if crud_action in _NO_500_ACTIONS:
                responses.pop(500, None)
            if crud_action in additional_responses:
                responses.update(additional_responses[crud_action])

            parameters = None
            if crud_action == "list":
                parameters = list_parameters
            elif crud_action in {
                "create",
                "retrieve",
                "update",
                "partial_update",
            }:
                parameters = (
                    [language_parameter] if language_parameter else None
                )

            config[crud_action] = extend_schema(
                operation_id=f"{tpl['op_prefix']}{model_name}",
                summary=tpl["summary"] % {"name": name_for_tpl},
                description=tpl["description"] % {"name": name_for_tpl},
                tags=[tag],
                parameters=parameters,
                responses={crud_default_status: None, **responses}
                if crud_default_status not in responses
                else responses,
            )

    return config
