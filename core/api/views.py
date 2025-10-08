import base64
import hashlib
import logging
import os
from django.core.exceptions import ImproperlyConfigured
from celery import Celery
from celery.exceptions import CeleryError
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from django.conf import settings
from django.db import DatabaseError, connection
from django.middleware.csrf import get_token
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiParameter,
)
from redis import Redis, RedisError
from rest_framework import status
from rest_framework.decorators import action, api_view
from rest_framework.metadata import SimpleMetadata
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from django.db import transaction
from core.api.serializers import HealthCheckResponseSerializer
from core.pagination.cursor import CursorPaginator
from core.pagination.limit_offset import LimitOffsetPaginator
from core.pagination.page_number import PageNumberPaginator
from core.utils.serializers import (
    RequestSerializersConfig,
    ResponseSerializersConfig,
)
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
    action = None
    default_actions = [
        "list",
        "retrieve",
        "create",
        "update",
        "partial_update",
        "destroy",
    ]
    request_serializers: RequestSerializersConfig = {}
    response_serializers: ResponseSerializersConfig = {}

    def get_serializer_class(self):
        """
        Return the serializer class for this action.

        This method is used by DRF's built-in methods and schema generation.
        It prioritizes response serializers since they're more commonly needed
        for general serialization tasks.
        """
        has_explicit_serializer_class = (
            hasattr(self, "serializer_class")
            and self.serializer_class is not None
        )

        # Handle edge case where action might be None or empty
        current_action = getattr(self, "action", None)
        if not current_action:
            if has_explicit_serializer_class:
                return self.serializer_class
            raise ImproperlyConfigured(
                f"No action defined for {self.__class__.__name__} and no default serializer_class. "
                "Ensure the view has a valid action or define serializer_class."
            )

        # Try to get response serializer for current action first
        if hasattr(self, "response_serializers") and self.response_serializers:
            response_serializer_class = self.response_serializers.get(
                current_action
            )
            if response_serializer_class is not None:
                return response_serializer_class

        # For write operations, try request serializers as fallback
        if (
            hasattr(self, "request")
            and self.request.method in ["POST", "PUT", "PATCH"]
            and hasattr(self, "request_serializers")
            and self.request_serializers
        ):
            request_serializer_class = self.request_serializers.get(
                current_action
            )
            if request_serializer_class is not None:
                return request_serializer_class

        # Fall back to the default serializer class if available
        if has_explicit_serializer_class:
            return self.serializer_class

        # If no serializer is found, raise an error
        raise ImproperlyConfigured(
            "No serializer found for action '{action}' and no default serializer defined. "
            "Define {cls}.response_serializers['{action}'], {cls}.request_serializers['{action}'], "
            "or set {cls}.serializer_class, or override {cls}.get_serializer_class().".format(
                action=current_action, cls=self.__class__.__name__
            )
        )

    def get_request_serializer(self, *args, **kwargs):
        """
        Get the serializer CLASS for request data validation.
        Returns a class, not an instance.

        Note: *args, **kwargs are kept for compatibility but not used
        since this returns a class.
        """
        if hasattr(self, "request_serializers") and self.request_serializers:
            request_serializer_class = self.request_serializers.get(self.action)
            if request_serializer_class is not None:
                return request_serializer_class

        # Fall back to the main serializer class
        return self.get_serializer_class()

    def get_response_serializer(self, *args, **kwargs):
        """
        Get the serializer CLASS for response data formatting.
        Returns a class, not an instance.

        Note: *args, **kwargs are kept for compatibility with existing code
        but are not used since this returns a class.
        """
        if hasattr(self, "response_serializers") and self.response_serializers:
            response_serializer_class = self.response_serializers.get(
                self.action
            )
            if response_serializer_class is not None:
                return response_serializer_class

        # Fall back to the main serializer class
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

            # Get request serializer class
            if (
                hasattr(self, "request_serializers")
                and self.request_serializers
            ):
                request_serializer_class = self.request_serializers.get(
                    action_name
                )

            # Get response serializer class
            if (
                hasattr(self, "response_serializers")
                and self.response_serializers
            ):
                response_serializer_class = self.response_serializers.get(
                    action_name
                )

            # Use the main get_serializer_class as fallback, but handle errors gracefully
            fallback_class = None
            try:
                fallback_class = self.get_serializer_class()
            except ImproperlyConfigured:
                # If no fallback is available, we'll handle this below
                pass

            if request_serializer_class is None:
                request_serializer_class = fallback_class
            if response_serializer_class is None:
                response_serializer_class = fallback_class

            # Ensure we have at least something for schema generation
            if (
                request_serializer_class is None
                and response_serializer_class is None
            ):
                raise ImproperlyConfigured(
                    f"No serializers found for action '{action_name}' in {self.__class__.__name__}. "
                    "Define request_serializers, response_serializers, or serializer_class."
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
        try:
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
        except Exception as e:
            logger.error(f"Error in create: {e}", exc_info=True)
            raise

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()

        try:
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
        except Exception as e:
            logger.error(f"Error in update: {e}", exc_info=True)
            raise

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
        connection.cursor()
        health_status["database"] = True
    except DatabaseError:
        health_status["database"] = False

    try:
        redis_conn = Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
        redis_conn.ping()
        health_status["redis"] = True
    except RedisError:
        health_status["redis"] = False

    try:
        celery_app = Celery(broker=settings.CELERY_BROKER_URL)
        celery_status = celery_app.control.ping()
        health_status["celery"] = bool(celery_status)
    except CeleryError:
        health_status["celery"] = False

    response = Response(health_status)
    get_token(request)
    return response


def encrypt_token(token: str, secret_key: str) -> str:
    key = hashlib.sha256(secret_key.encode()).digest()
    nonce = os.urandom(16)
    cipher = Cipher(
        algorithms.AES(key), modes.GCM(nonce), backend=default_backend()
    )
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(token.encode()) + encryptor.finalize()
    encrypted_token = base64.urlsafe_b64encode(
        nonce + encryptor.tag + ciphertext
    ).decode("utf-8")
    return encrypted_token


def redirect_to_frontend(request, *args, **kwargs):
    from knox.models import get_token_model  # noqa: PLC0415

    AuthToken = get_token_model()
    user = request.user

    if user.is_authenticated:
        _, token = AuthToken.objects.create(user)
        encrypted_token = encrypt_token(token, settings.SECRET_KEY)
    else:
        encrypted_token = ""

    frontend_url = settings.NUXT_BASE_URL
    redirect_path = "/account/provider/callback"
    response = redirect(
        f"{frontend_url}{redirect_path}?encrypted_token={encrypted_token}"
    )

    response.headers["X-Encrypted-Token"] = encrypted_token

    return response
