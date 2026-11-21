"""The store-settings endpoints: the staff list, one setting by key, and
the public settings payload the storefront reads."""

import json
import logging

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    permission_classes,
)
from rest_framework.response import Response

from core.api.permissions import IsStoreStaff
from core.api.serializers import (
    ErrorResponseSerializer,
    PublicSettingsSerializer,
    SettingDetailSerializer,
    SettingSerializer,
)
from core.store_settings import PUBLIC_SETTING_KEYS
from tenant.membership import is_store_staff

logger = logging.getLogger(__name__)


# ``ty: ignore[invalid-argument-type]`` on each ``@extend_schema`` below: a
# ty limitation with ``djangorestframework-stubs``' ``AsView``, explained in
# ``core/api/health_api.py``.
@extend_schema(  # ty: ignore[invalid-argument-type]
    summary=_("List all available settings"),
    description=_("Retrieve all settings with their names, values, and types"),
    tags=["Settings"],
    responses={
        200: SettingSerializer(many=True),
        500: ErrorResponseSerializer,
    },
)
@api_view(["GET"])
# Per-store data, so the store's own staff may read it. It was
# gated on IsPlatformSuperuser, which on a tenant host reads
# is_superuser off a tenant-schema row — see is_platform_superuser.
@permission_classes([IsStoreStaff])
def list_settings(request):
    """List all available settings with their values."""
    try:
        from extra_settings.models import Setting

        settings_list = []
        all_settings = Setting.objects.all()

        for setting in all_settings:
            settings_list.append(
                {
                    "name": setting.name,
                    "value": str(setting.value),
                    "type": setting.value_type,
                }
            )

        from core.api.serializers import SettingSerializer

        serializer = SettingSerializer(settings_list, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    except Exception:
        logger.exception("Error listing settings")

        return Response(
            {
                "detail": _("Failed to retrieve settings"),
                "code": "settings_unavailable",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@extend_schema(  # ty: ignore[invalid-argument-type]
    summary=_("Get setting by key"),
    description=_("Retrieve a specific setting value by its key name"),
    tags=["Settings"],
    parameters=[
        OpenApiParameter(
            name="key",
            description=_("Setting key name (e.g., GIFT_CARD_MIN_AMOUNT)"),
            required=True,
            type=str,
            location=OpenApiParameter.QUERY,
        ),
    ],
    responses={
        200: SettingDetailSerializer,
        404: ErrorResponseSerializer,
        500: ErrorResponseSerializer,
    },
)
@api_view(["GET"])
def get_setting_by_key(request):
    """Get a specific setting by its key name.

    Public (whitelisted) keys are accessible without authentication.
    All other keys require admin access.
    """
    try:
        from core.store_settings import DECLARED_DEFAULTS, store_setting

        key = request.query_params.get("key")

        if not key:
            error_data = {
                "detail": _("Setting key is required"),
                "error": "missing_key",
                "code": "missing_key",
            }
            return Response(
                error_data,
                status=status.HTTP_400_BAD_REQUEST,
            )

        # This endpoint serves the store settings the platform declares,
        # nothing else. `key` comes straight off the query string, and
        # an unbounded lookup once read the Django settings module
        # through django-extra-settings' conf fallback: any staff token
        # got `SECRET_KEY`. That fallback is off now, and an undeclared
        # name is indistinguishable from one that does not exist.
        if key not in DECLARED_DEFAULTS:
            return Response(
                {
                    "detail": _("Setting not found or access denied."),
                    "code": "setting_not_found",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if (key not in PUBLIC_SETTING_KEYS) and (
            not is_store_staff(request.user)
        ):
            return Response(
                {
                    "detail": _("Setting not found or access denied."),
                    "code": "setting_not_found",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = SettingDetailSerializer(
            {
                "name": key,
                "value": _serialize_setting_value(store_setting(key)),
            }
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    except Exception:
        logger.exception("Error retrieving setting")

        return Response(
            {
                "detail": _("Failed to retrieve setting"),
                "code": "setting_unavailable",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


def _serialize_setting_value(value) -> str:
    # json-typed settings hold dicts/lists — ``str()`` would produce a
    # Python repr (single quotes) the storefront can't JSON.parse.
    if isinstance(value, dict | list):
        return json.dumps(value)
    return str(value)


@extend_schema(  # ty: ignore[invalid-argument-type]
    summary=_("Get public settings"),
    description=_(
        "Retrieve every anonymously readable store setting in one call, "
        "keyed by setting name: every PUBLIC_SETTING_KEYS entry, with "
        "the store's value or, when the store has none, the value the "
        "platform declares for it."
    ),
    tags=["Settings"],
    responses={
        200: PublicSettingsSerializer,
        500: ErrorResponseSerializer,
    },
)
@api_view(["GET"])
def get_public_settings(request):
    """The storefront's ONE settings read per render.

    Every server render used to fetch its feature flags and store
    values one key at a time — around thirty ``settings/get`` round
    trips per page — which is what saturated the storefront under a
    crawler burst (Ahrefs, 2026-09-11). One query in the request's
    tenant schema replaces them; the storefront caches the payload per
    tenant.

    Every key is present with its effective value: the store's, else
    the one the platform declares. So no client keeps a default of its
    own that can drift from the declared one.
    """
    try:
        from contact.attachments import parse_attachment_types
        from core.store_setting_shapes import (
            public_scalar_values,
            public_structured_values,
        )
        from core.store_settings import store_settings

        raw = store_settings(PUBLIC_SETTING_KEYS)
        typed = public_structured_values(raw)
        serializer = PublicSettingsSerializer(
            {
                "settings": {
                    key: _serialize_setting_value(value)
                    for key, value in sorted(raw.items())
                },
                "announcement_bar": typed["ANNOUNCEMENT_BAR"],
                "auth_panel": typed["AUTH_PANEL"],
                "funding_banner": typed["FUNDING_BANNER"],
                "store_offices": typed["STORE_OFFICES"] or [],
                "business_hours": typed["BUSINESS_HOURS"],
                "values": public_scalar_values(raw),
                "contact_attachments_types": list(
                    parse_attachment_types(raw["CONTACT_ATTACHMENTS_TYPES"])
                ),
            }
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    except Exception:
        logger.exception("Error retrieving public settings")

        return Response(
            {
                "detail": _("Failed to retrieve settings"),
                "code": "settings_unavailable",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
