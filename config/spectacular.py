from os import getenv

SPECTACULAR_SETTINGS = {
    "TITLE": getenv("DJANGO_SPECTACULAR_SETTINGS_TITLE", "Django"),
    "DESCRIPTION": getenv("DJANGO_SPECTACULAR_SETTINGS_DESCRIPTION", "Django"),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAuthenticated"],
    "AUTHENTICATION_WHITELIST": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.BasicAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "SERVE_AUTHENTICATION": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.BasicAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.contrib.djangorestframework_camel_case.camelize_serializer_fields",
        "drf_spectacular.hooks.postprocess_schema_enums",
    ],
    "ENUM_NAME_OVERRIDES": {
        # Used by Checkout, Order, OrderCreateUpdate, PatchedOrderCreateUpdate
        "OrderStatusEnum": "order.enum.status_enum.OrderStatusEnum",
    },
}
