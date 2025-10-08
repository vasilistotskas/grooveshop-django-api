import json
import re
from collections.abc import Mapping
from typing import Any, NotRequired, TypedDict

from django.db import models
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from parler_rest.fields import TranslatedFieldsField
from rest_framework import serializers
from drf_spectacular.utils import OpenApiParameter


class TranslatedFieldExtended(TranslatedFieldsField):
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


type RequestSerializersConfig = dict[str, type[serializers.Serializer]]
type ResponseSerializersConfig = dict[str, type[serializers.Serializer]]


def create_schema_view_config(
    model_class: type[models.Model],
    request_serializers: RequestSerializersConfig | None = None,
    response_serializers: ResponseSerializersConfig | None = None,
    error_serializer=None,
    additional_responses=None,
    display_config: DisplayConfig | Mapping[str, Any] | None = None,
    include_language_param: bool = True,
    include_pagination_params: bool = True,
):
    """
    Create schema configuration for a ViewSet with translation support.

    Args:
        model_class: The Django model class
        request_serializers: Dict with keys: create, update, partial_update
        response_serializers: Dict with keys: create, list, retrieve, update, partial_update, destroy
        error_serializer: Serializer for error responses
        additional_responses: Dict with additional responses per action
        display_config: DisplayConfig or dict with optional keys:
            - tag: Override for OpenAPI tag (default: model's verbose_name_plural)
            - display_name: Override for singular name (default: model's verbose_name)
            - display_name_plural: Override for plural name (default: model's verbose_name_plural)
        include_language_param: Whether to include the language query parameter for translation-enabled models
        include_pagination_params: Whether to include pagination query parameters for list endpoints

    Note:
        This function fully supports Django translations. If your model uses
        gettext_lazy for verbose_name/verbose_name_plural, all generated
        summaries and descriptions will be properly translated.
    """
    if request_serializers is None:
        request_serializers = {}
    if response_serializers is None:
        response_serializers = {}
    if additional_responses is None:
        additional_responses = {}
    if display_config is None:
        display_config = {}

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

    base_error_responses = (
        {
            400: error_serializer,
            401: error_serializer,
            403: error_serializer,
            404: error_serializer,
            500: error_serializer,
        }
        if error_serializer
        else {}
    )

    language_parameter = None
    if include_language_param:
        from django.conf import settings

        try:
            available_languages = [
                lang["code"]
                for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
            ]
            default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
            language_parameter = OpenApiParameter(
                name="language_code",
                description=_("Language code for translations (%s)")
                % ", ".join(available_languages),
                required=False,
                type=str,
                enum=available_languages,
                default=default_language,
            )
        except (AttributeError, KeyError):
            language_parameter = None

    pagination_parameters = []
    if include_pagination_params:
        pagination_type_parameter = OpenApiParameter(
            name="pagination_type",
            description=_("Pagination strategy type"),
            required=False,
            type=str,
            enum=["pageNumber", "cursor", "limitOffset"],
            default="pageNumber",
        )

        pagination_parameter = OpenApiParameter(
            name="pagination",
            description=_("Enable or disable pagination"),
            required=False,
            type=str,
            enum=["true", "false"],
            default="true",
        )

        page_size_parameter = OpenApiParameter(
            name="page_size",
            description=_("Number of results to return per page"),
            required=False,
            type=int,
            default=20,
        )

        pagination_parameters = [
            pagination_type_parameter,
            pagination_parameter,
            page_size_parameter,
        ]

    list_parameters = []
    if language_parameter:
        list_parameters.append(language_parameter)
    list_parameters.extend(pagination_parameters)

    create_request_serializer = request_serializers.get("create")
    update_request_serializer = request_serializers.get("update")
    partial_update_request_serializer = request_serializers.get(
        "partial_update"
    )

    create_response_serializer = response_serializers.get("create")
    list_response_serializer = response_serializers.get("list")
    retrieve_response_serializer = response_serializers.get("retrieve")
    update_response_serializer = response_serializers.get("update")
    partial_update_response_serializer = response_serializers.get(
        "partial_update"
    )
    destroy_response_serializer = response_serializers.get("destroy")

    config = {
        "list": extend_schema(
            operation_id=f"list{model_name}",
            summary=_("List %(name)s") % {"name": display_name_plural},
            description=_(
                "Retrieve a list of %(name)s with filtering and search capabilities. "
                "Supports multiple pagination strategies: pageNumber (default), cursor, and limitOffset."
            )
            % {"name": display_name_plural},
            tags=[tag],
            parameters=list_parameters,
            responses={
                200: list_response_serializer(many=True)
                if list_response_serializer
                else None,
                **base_error_responses,
                **additional_responses.get("list", {}),
            },
        ),
        "create": extend_schema(
            operation_id=f"create{model_name}",
            summary=_("Create a %(name)s") % {"name": display_name},
            description=_("Create a new %(name)s. Requires authentication.")
            % {"name": display_name},
            tags=[tag],
            parameters=[language_parameter] if language_parameter else None,
            request=create_request_serializer,
            responses={
                201: create_response_serializer,
                **base_error_responses,
                **additional_responses.get("create", {}),
            },
        ),
        "retrieve": extend_schema(
            operation_id=f"retrieve{model_name}",
            summary=_("Retrieve a %(name)s") % {"name": display_name},
            description=_("Get detailed information about a specific %(name)s.")
            % {"name": display_name},
            tags=[tag],
            parameters=[language_parameter] if language_parameter else None,
            responses={
                200: retrieve_response_serializer,
                **{k: v for k, v in base_error_responses.items() if k != 400},
                **additional_responses.get("retrieve", {}),
            },
        ),
        "update": extend_schema(
            operation_id=f"update{model_name}",
            summary=_("Update a %(name)s") % {"name": display_name},
            description=_(
                "Update %(name)s information. Requires authentication."
            )
            % {"name": display_name},
            tags=[tag],
            parameters=[language_parameter] if language_parameter else None,
            request=update_request_serializer,
            responses={
                200: update_response_serializer,
                **base_error_responses,
                **additional_responses.get("update", {}),
            },
        ),
        "partial_update": extend_schema(
            operation_id=f"partialUpdate{model_name}",
            summary=_("Partially update a %(name)s") % {"name": display_name},
            description=_(
                "Partially update %(name)s information. Requires authentication."
            )
            % {"name": display_name},
            tags=[tag],
            parameters=[language_parameter] if language_parameter else None,
            request=partial_update_request_serializer,
            responses={
                200: partial_update_response_serializer,
                **base_error_responses,
                **additional_responses.get("partial_update", {}),
            },
        ),
        "destroy": extend_schema(
            operation_id=f"destroy{model_name}",
            summary=_("Delete a %(name)s") % {"name": display_name},
            description=_("Delete a %(name)s. Requires authentication.")
            % {"name": display_name},
            tags=[tag],
            responses={
                204: destroy_response_serializer,
                **{
                    k: v
                    for k, v in base_error_responses.items()
                    if k not in [400, 500]
                },
                **additional_responses.get("destroy", {}),
            },
        ),
    }

    return config
