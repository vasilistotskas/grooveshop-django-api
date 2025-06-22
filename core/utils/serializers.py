import json
import re
from collections.abc import Mapping
from typing import Any, NotRequired, TypedDict

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from parler_rest.fields import TranslatedFieldsField
from rest_framework import serializers as rf_serializers


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
            raise rf_serializers.ValidationError(errors)
        return result


def camel_to_words(name):
    result = re.sub(r"(?<!^)(?=[A-Z])", " ", name)
    return result


class DisplayConfig(TypedDict):
    tag: NotRequired[str]
    display_name: NotRequired[str]
    display_name_plural: NotRequired[str]


def create_schema_view_config(
    model_class: type[models.Model],
    serializers: dict | None = None,
    error_serializer=None,
    additional_responses=None,
    display_config: DisplayConfig | Mapping[str, Any] | None = None,
):
    """
    Create schema configuration for a ViewSet with translation support.

    Args:
        model_class: The Django model class
        serializers: Dict with keys: list_serializer, detail_serializer, write_serializer
        error_serializer: Serializer for error responses
        additional_responses: Dict with additional responses per action
        display_config: DisplayConfig or dict with optional keys:
            - tag: Override for OpenAPI tag (default: model's verbose_name_plural)
            - display_name: Override for singular name (default: model's verbose_name)
            - display_name_plural: Override for plural name (default: model's verbose_name_plural)

    Note:
        This function fully supports Django translations. If your model uses
        gettext_lazy for verbose_name/verbose_name_plural, all generated
        summaries and descriptions will be properly translated.
    """
    if serializers is None:
        serializers = {}
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

    list_serializer = serializers.get("list_serializer")
    detail_serializer = serializers.get("detail_serializer")
    write_serializer = serializers.get("write_serializer")

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

    config = {
        "list": extend_schema(
            operation_id=f"list{model_name}",
            summary=_("List %(name)s") % {"name": display_name_plural},
            description=_(
                "Retrieve a list of %(name)s with filtering and search capabilities."
            )
            % {"name": display_name_plural},
            tags=[tag],
            responses={
                200: list_serializer(many=True) if list_serializer else None,
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
            request=write_serializer,
            responses={
                201: detail_serializer,
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
            responses={
                200: detail_serializer,
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
            request=write_serializer,
            responses={
                200: detail_serializer,
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
            request=write_serializer,
            responses={
                200: detail_serializer,
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
                204: None,
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


class MultiSerializerMixin:
    action = None
    serializers = {
        "default": None,
        "list": None,
        "create": None,
        "retrieve": None,
        "update": None,
        "partial_update": None,
        "destroy": None,
    }
    request_serializers = {}
    response_serializers = {}

    def get_serializer_class(self):
        has_explicit_serializer_class = (
            hasattr(self, "serializer_class")
            and self.serializer_class is not None
        )

        if hasattr(self, "serializers") and has_explicit_serializer_class:
            raise ImproperlyConfigured(
                "{cls} should only define either `serializer_class` or "
                "`serializers`.".format(cls=self.__class__.__name__)
            )

        if not hasattr(self, "serializers") or self.serializers is None:
            raise ImproperlyConfigured(
                "{cls} is missing the serializers attribute. Define "
                "{cls}.serializers, or override "
                "{cls}.get_serializer_class().".format(
                    cls=self.__class__.__name__
                )
            )

        serializer_class = self.serializers.get(self.action)
        if serializer_class is None:
            serializer_class = self.serializers.get("default")

        if serializer_class is None:
            raise ImproperlyConfigured(
                "No serializer found for action '{action}' and no default serializer defined. "
                "Define {cls}.serializers['{action}'] or {cls}.serializers['default'], "
                "or override {cls}.get_serializer_class().".format(
                    action=self.action, cls=self.__class__.__name__
                )
            )

        return serializer_class

    def get_request_serializer_class(self):
        if hasattr(self, "request_serializers") and self.request_serializers:
            request_serializer = self.request_serializers.get(self.action)
            if request_serializer is not None:
                return request_serializer

        return self.get_serializer_class()

    def get_response_serializer_class(self):
        if hasattr(self, "response_serializers") and self.response_serializers:
            response_serializer = self.response_serializers.get(self.action)
            if response_serializer is not None:
                return response_serializer

        return self.get_serializer_class()

    def get_serializer_for_schema(self, action_name=None):
        if action_name is None:
            action_name = self.action

        original_action = self.action
        self.action = action_name

        try:
            request_serializer = self.get_request_serializer_class()
            response_serializer = self.get_response_serializer_class()
            return {
                "request": request_serializer,
                "response": response_serializer,
            }
        finally:
            self.action = original_action

    def get_serializer_context(self):
        context = (
            super().get_serializer_context()
            if hasattr(super(), "get_serializer_context")
            else {}
        )
        context.update(
            {
                "action": self.action,
                "view": self,
            }
        )
        return context
