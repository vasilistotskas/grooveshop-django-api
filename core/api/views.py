import logging
from django.core.exceptions import ImproperlyConfigured
from celery.exceptions import CeleryError
from core.celery import celery_app
from django.conf import settings
from django.db import DatabaseError, connection
from django.middleware.csrf import get_token
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiParameter,
)
from redis import Redis, RedisError
from rest_framework import status
from rest_framework.decorators import (
    action,
    api_view,
    permission_classes,
)
from rest_framework.permissions import IsAdminUser
from rest_framework.metadata import SimpleMetadata
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from django.db import transaction
from core.api.serializers import (
    ErrorResponseSerializer,
    HealthCheckResponseSerializer,
    SettingDetailSerializer,
    SettingSerializer,
)
from core.pagination.cursor import CursorPaginator
from core.pagination.limit_offset import LimitOffsetPaginator
from core.pagination.page_number import PageNumberPaginator
from core.utils.serializers import SerializersConfig
from core.utils.views import TranslationsProcessingMixin

logger = logging.getLogger(__name__)

default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]

LANGUAGE_PARAMETER = OpenApiParameter(
    name="language_code",
    description=_("Language code for translations (%s)")
    % ", ".join(available_languages),
    required=False,
    type=str,
    enum=available_languages,
    default=default_language,
)

PAGINATION_TYPE_PARAMETER = OpenApiParameter(
    name="pagination_type",
    description=_("Pagination strategy type"),
    required=False,
    type=str,
    enum=["pageNumber", "cursor", "limitOffset"],
    default="pageNumber",
)

PAGINATION_PARAMETER = OpenApiParameter(
    name="pagination",
    description=_("Enable or disable pagination"),
    required=False,
    type=str,
    enum=["true", "false"],
    default="true",
)

PAGE_SIZE_PARAMETER = OpenApiParameter(
    name="page_size",
    description=_("Number of results to return per page"),
    required=False,
    type=int,
    default=20,
)

CURSOR_PARAMETER = OpenApiParameter(
    name="cursor",
    description=_("Cursor for pagination"),
    required=False,
    type=str,
)


class RequestResponseSerializerMixin:
    action: str | None = None
    default_actions = [
        "list",
        "retrieve",
        "create",
        "update",
        "partial_update",
        "destroy",
    ]
    serializers_config: SerializersConfig = {}

    def get_serializer_class(self):
        """
        Return the serializer class for this action.

        Prioritizes response serializers since they're more commonly needed
        for general serialization tasks.
        """
        has_explicit_serializer_class = (
            hasattr(self, "serializer_class")
            and self.serializer_class is not None
        )

        current_action = getattr(self, "action", None)
        if not current_action:
            if has_explicit_serializer_class:
                return self.serializer_class
            raise ImproperlyConfigured(
                f"No action defined for {self.__class__.__name__} and no default serializer_class. "
                "Ensure the view has a valid action or define serializer_class."
            )

        cfg = self.serializers_config.get(current_action)
        if cfg:
            if cfg.response is not None:
                return cfg.response
            if cfg.request is not None:
                return cfg.request

        if has_explicit_serializer_class:
            return self.serializer_class

        raise ImproperlyConfigured(
            "No serializer found for action '{action}' and no default serializer defined. "
            "Define {cls}.serializers_config['{action}'] or set {cls}.serializer_class, "
            "or override {cls}.get_serializer_class().".format(
                action=current_action, cls=self.__class__.__name__
            )
        )

    def get_request_serializer(self, *args, **kwargs):
        """
        Get the serializer CLASS for request data validation.
        Returns a class, not an instance.
        """
        cfg = self.serializers_config.get(self.action) if self.action else None
        if cfg and cfg.request is not None:
            return cfg.request
        return self.get_serializer_class()

    def get_response_serializer(self, *args, **kwargs):
        """
        Get the serializer CLASS for response data formatting.
        Returns a class, not an instance.
        """
        cfg = self.serializers_config.get(self.action) if self.action else None
        if cfg and cfg.response is not None:
            return cfg.response
        return self.get_serializer_class()

    def get_serializer_for_schema(self, action_name=None):
        """
        Get serializer classes for OpenAPI schema generation.
        Returns a dict with 'request' and 'response' keys containing classes.
        """
        if action_name is None:
            action_name = self.action

        original_action = self.action
        self.action = action_name

        try:
            request_serializer_class = None
            response_serializer_class = None

            cfg = (
                self.serializers_config.get(action_name)
                if action_name
                else None
            )
            if cfg:
                request_serializer_class = cfg.request
                response_serializer_class = cfg.response

            fallback_class = None
            try:
                fallback_class = self.get_serializer_class()
            except ImproperlyConfigured:
                pass

            if request_serializer_class is None:
                request_serializer_class = fallback_class
            if response_serializer_class is None:
                response_serializer_class = fallback_class

            if (
                request_serializer_class is None
                and response_serializer_class is None
            ):
                raise ImproperlyConfigured(
                    f"No serializers found for action '{action_name}' in {self.__class__.__name__}. "
                    "Define serializers_config or serializer_class."
                )

            return {
                "request": request_serializer_class,
                "response": response_serializer_class,
            }
        finally:
            self.action = original_action

    def get_serializer_context(self):
        """
        Extra context provided to the serializer class.
        """
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


class Metadata(SimpleMetadata):
    def determine_metadata(self, request, view):
        metadata = super().determine_metadata(request, view)
        metadata["filterset_fields"] = getattr(view, "filterset_fields", [])
        metadata["ordering_fields"] = getattr(view, "ordering_fields", [])
        metadata["ordering"] = getattr(view, "ordering", [])
        metadata["search_fields"] = getattr(view, "search_fields", [])
        return metadata


class PaginationModelViewSet(ModelViewSet):
    @property
    def paginator(self):
        if not hasattr(self, "_paginator"):
            if self.pagination_class is None:
                self._paginator = None
            else:
                pagination_type = self.request.query_params.get(
                    "pagination_type", "pageNumber"
                ).lower()
                paginator_mapping = {
                    "pagenumber": PageNumberPaginator,
                    "page_number": PageNumberPaginator,
                    "limitoffset": LimitOffsetPaginator,
                    "limit_offset": LimitOffsetPaginator,
                    "cursor": CursorPaginator,
                }
                self._paginator = paginator_mapping.get(
                    pagination_type, PageNumberPaginator
                )()

        return self._paginator

    def paginate_and_serialize(
        self, queryset, request, many=True, serializer_class=None
    ):
        pagination_param = request.query_params.get(
            "pagination", "true"
        ).lower()

        if serializer_class is None:
            serializer_class = self.get_serializer_class()

        if serializer_class is None and hasattr(
            self, "get_response_serializer"
        ):
            serializer_class = self.get_response_serializer()

        if pagination_param == "false":
            serializer = serializer_class(
                queryset, many=many, context=self.get_serializer_context()
            )
            return Response(serializer.data, status=status.HTTP_200_OK)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = serializer_class(
                page, many=many, context=self.get_serializer_context()
            )
            return self.get_paginated_response(serializer.data)

        serializer = serializer_class(
            queryset, many=many, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


class TranslationsModelViewSet(TranslationsProcessingMixin, ModelViewSet):
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["language_code"] = self.request.query_params.get(
            "language_code", default_language
        )
        return context

    def create(self, request, *args, **kwargs):
        request = self.process_translations_data(request)
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        request = self.process_translations_data(request)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            LANGUAGE_PARAMETER,
            PAGINATION_TYPE_PARAMETER,
            PAGINATION_PARAMETER,
            PAGE_SIZE_PARAMETER,
            CURSOR_PARAMETER,
        ]
    ),
    retrieve=extend_schema(parameters=[LANGUAGE_PARAMETER]),
    create=extend_schema(parameters=[LANGUAGE_PARAMETER]),
    update=extend_schema(parameters=[LANGUAGE_PARAMETER]),
    partial_update=extend_schema(parameters=[LANGUAGE_PARAMETER]),
)
class BaseModelViewSet(
    RequestResponseSerializerMixin,
    TranslationsModelViewSet,
    PaginationModelViewSet,
):
    metadata_class = Metadata

    def create(self, request, *args, **kwargs):
        with transaction.atomic():
            req_serializer = self.get_request_serializer()
            request_serializer = req_serializer(
                data=request.data, context=self.get_serializer_context()
            )
            request_serializer.is_valid(raise_exception=True)
            self.perform_create(request_serializer)

            response_serializer_class = self.get_response_serializer()
            response_serializer = response_serializer_class(
                request_serializer.instance,
                context=self.get_serializer_context(),
            )

            headers = self.get_success_headers(response_serializer.data)
            return Response(
                response_serializer.data,
                status=status.HTTP_201_CREATED,
                headers=headers,
            )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()

        with transaction.atomic():
            req_serializer = self.get_request_serializer()
            request_serializer = req_serializer(
                instance,
                data=request.data,
                partial=partial,
                context=self.get_serializer_context(),
            )
            request_serializer.is_valid(raise_exception=True)
            self.perform_update(request_serializer)

            if getattr(instance, "_prefetched_objects_cache", None):
                instance._prefetched_objects_cache = {}

            response_serializer_class = self.get_response_serializer()
            response_serializer = response_serializer_class(
                request_serializer.instance,
                context=self.get_serializer_context(),
            )
            return Response(response_serializer.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        response_serializer_class = self.get_response_serializer()
        response_serializer = response_serializer_class(
            instance, context=self.get_serializer_context()
        )
        return Response(response_serializer.data)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        response_serializer_class = self.get_response_serializer()

        return self.paginate_and_serialize(
            queryset, request, serializer_class=response_serializer_class
        )

    @action(detail=False, methods=["GET"])
    def api_schema(self, request, *args, **kwargs):
        meta = self.metadata_class()
        data = meta.determine_metadata(request, self)
        return Response(data)


@extend_schema(
    summary=_("Check the health status of database, Redis, and Celery"),
    description=_("Check the health status of database, Redis, and Celery"),
    tags=["Health"],
    responses=HealthCheckResponseSerializer,
)
@api_view(["GET"])
def health_check(request):
    health_status = {
        "database": False,
        "redis": False,
        "celery": False,
    }

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        health_status["database"] = True
    except DatabaseError:
        health_status["database"] = False

    try:
        redis_conn = Redis.from_url(settings.REDIS_URL)
        try:
            redis_conn.ping()
            health_status["redis"] = True
        finally:
            redis_conn.close()
    except RedisError:
        health_status["redis"] = False

    try:
        celery_status = celery_app.control.ping(timeout=3)
        health_status["celery"] = bool(celery_status)
    except CeleryError:
        health_status["celery"] = False

    response = Response(health_status)
    get_token(request)
    return response


@extend_schema(
    summary=_("List all available settings"),
    description=_("Retrieve all settings with their names, values, and types"),
    tags=["Settings"],
    responses={
        200: SettingSerializer(many=True),
        500: ErrorResponseSerializer,
    },
)
@api_view(["GET"])
@permission_classes([IsAdminUser])
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

    except Exception as e:
        logger.error(f"Error listing settings: {e}", exc_info=True)

        return Response(
            {"detail": _("Failed to retrieve settings")},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


PUBLIC_SETTING_KEYS = frozenset(
    {
        "CHECKOUT_SHIPPING_PRICE",
        "FREE_SHIPPING_THRESHOLD",
        "LOYALTY_ENABLED",
        "LOYALTY_REDEMPTION_RATIO_EUR",
        "LOYALTY_POINTS_FACTOR",
        "LOYALTY_TIER_MULTIPLIER_ENABLED",
        "LOYALTY_POINTS_EXPIRATION_DAYS",
        "LOYALTY_NEW_CUSTOMER_BONUS_ENABLED",
        "LOYALTY_NEW_CUSTOMER_BONUS_POINTS",
        "LOYALTY_XP_PER_LEVEL",
        "B2B_INVOICING_ENABLED",
    }
)


@extend_schema(
    summary=_("Get setting by key"),
    description=_("Retrieve a specific setting value by its key name"),
    tags=["Settings"],
    parameters=[
        OpenApiParameter(
            name="key",
            description=_("Setting key name (e.g., CHECKOUT_SHIPPING_PRICE)"),
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
        from extra_settings.models import Setting

        key = request.query_params.get("key")

        if not key:
            error_data = {
                "detail": _("Setting key is required"),
                "error": "missing_key",
            }
            return Response(
                error_data,
                status=status.HTTP_400_BAD_REQUEST,
            )

        if key not in PUBLIC_SETTING_KEYS:
            if not (
                request.user
                and request.user.is_authenticated
                and request.user.is_staff
            ):
                return Response(
                    {"detail": _("Setting not found or access denied.")},
                    status=status.HTTP_404_NOT_FOUND,
                )

        try:
            setting_value = Setting.get(key)
            from core.api.serializers import SettingDetailSerializer

            serializer = SettingDetailSerializer(
                {
                    "name": key,
                    "value": str(setting_value),
                }
            )
            return Response(serializer.data, status=status.HTTP_200_OK)

        except Setting.DoesNotExist:
            error_data = {
                "detail": _("Setting '%(key)s' not found") % {"key": key},
                "error": "not_found",
            }
            return Response(
                error_data,
                status=status.HTTP_404_NOT_FOUND,
            )

    except Exception as e:
        logger.error(f"Error retrieving setting: {e}", exc_info=True)

        return Response(
            {"detail": _("Failed to retrieve setting")},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
