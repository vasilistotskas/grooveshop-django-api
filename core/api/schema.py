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


def generate_schema_multi_lang(model_instance):
    fields = {}
    translated_fields = model_instance._parler_meta.get_all_fields()
    languages = settings.PARLER_LANGUAGES[settings.SITE_ID]

    if not translated_fields or not languages:
        return {"type": "object", "properties": {}}

    for language in languages:
        fields[language["code"]] = {"type": "object", "properties": {}}
        for translated_field in translated_fields:
            fields[language["code"]]["properties"][translated_field] = {
                "type": "string"
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

            for key, value in schema_dict.items():
                if isinstance(value, (dict, list)):
                    process_schema_recursive(value)
        elif isinstance(schema_dict, list):
            for item in schema_dict:
                if isinstance(item, (dict, list)):
                    process_schema_recursive(item)

    process_schema_recursive(result)
    return result
