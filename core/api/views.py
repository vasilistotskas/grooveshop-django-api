import base64
import hashlib
import os

from celery import Celery
from celery.exceptions import CeleryError
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from django.conf import settings
from django.db import DatabaseError, connection
from django.middleware.csrf import get_token
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from redis import Redis, RedisError
from rest_framework import status
from rest_framework.decorators import action, api_view
from rest_framework.metadata import SimpleMetadata
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.api.serializers import HealthCheckResponseSerializer
from core.pagination.cursor import CursorPaginator
from core.pagination.limit_offset import LimitOffsetPaginator
from core.pagination.page_number import PageNumberPaginator
from core.utils.views import TranslationsProcessingMixin

default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


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
                    "pagination_type", ""
                ).lower()
                paginator_mapping = {
                    "page_number": PageNumberPaginator,
                    "limit_offset": LimitOffsetPaginator,
                    "cursor": CursorPaginator,
                }
                self._paginator = paginator_mapping.get(
                    pagination_type, self.pagination_class
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
        context["language"] = self.request.query_params.get(
            "language", default_language
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


class BaseModelViewSet(TranslationsModelViewSet, PaginationModelViewSet):
    metadata_class = Metadata

    def get_response_serializer_class(self):
        if hasattr(super(), "get_response_serializer_class"):
            return super().get_response_serializer_class()
        return self.get_serializer_class()

    def get_request_serializer_class(self):
        if hasattr(super(), "get_request_serializer_class"):
            return super().get_request_serializer_class()
        return self.get_serializer_class()

    def create(self, request, *args, **kwargs):
        request_serializer_class = self.get_request_serializer_class()
        serializer = request_serializer_class(
            data=request.data, context=self.get_serializer_context()
        )
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)

        response_serializer_class = self.get_response_serializer_class()
        response_serializer = response_serializer_class(
            serializer.instance, context=self.get_serializer_context()
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

        request_serializer_class = self.get_request_serializer_class()
        serializer = request_serializer_class(
            instance,
            data=request.data,
            partial=partial,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        if getattr(instance, "_prefetched_objects_cache", None):
            instance._prefetched_objects_cache = {}

        response_serializer_class = self.get_response_serializer_class()
        response_serializer = response_serializer_class(
            serializer.instance, context=self.get_serializer_context()
        )
        return Response(response_serializer.data)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        response_serializer_class = self.get_response_serializer_class()
        serializer = response_serializer_class(
            instance, context=self.get_serializer_context()
        )
        return Response(serializer.data)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        return self.paginate_and_serialize(
            queryset, request, serializer_class=self.get_serializer_class()
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
